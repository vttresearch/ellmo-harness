using System;
using System.Collections;
using Ellmo.XR.CameraFeed;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityPdfViewer;

public class AgentToolExecutor : MonoBehaviour
{
    [Header("Backend")]
    [SerializeField] private EllmoToolsManager ellmo;

    [Header("Scene References")]
    [SerializeField] private Renderer colorRenderer;
    [SerializeField] private PdfViewerUI pdfViewerUI;

    [Header("User POV")]
    [Tooltip("Component implementing IXRCameraFeed. The current raw camera texture is returned to the backend.")]
    [SerializeField] private MonoBehaviour userPovSource;
    [SerializeField, Range(1, 100)] private int userPovJpegQuality = 85;
    [SerializeField, Min(1f)] private float userPovCaptureTimeoutSeconds = 15f;

    private IXRCameraFeed _userPovFeed;
    private ImageCaptureFeedback _imageCaptureFeedback;

    private void Start()
    {
        _userPovFeed = userPovSource as IXRCameraFeed;
        if (userPovSource != null && _userPovFeed == null)
            Debug.LogError("[AgentToolExecutor] User POV source must implement IXRCameraFeed.");

        if (ellmo == null)
        {
            Debug.LogError("[AgentAffordance] EllmoToolsManager reference is missing.");
            return;
        }

        // Listen for tool calls/log messages from the backend.
        ellmo.OnLogMessage += HandleToolInvocation;
    }

    private void OnDestroy()
    {
        if (ellmo != null)
            ellmo.OnLogMessage -= HandleToolInvocation;
    }

    private void HandleToolInvocation(JToken token)
    {
        if (token is not JObject tokenObject)
            return;

        // We only care about Unity tool invocations here.
        if (!string.Equals(tokenObject["type"]?.Value<string>(), "unity_tool", StringComparison.Ordinal))
            return;

        var toolName = tokenObject["data"]?["tool_name"]?.Value<string>();
        if (string.IsNullOrEmpty(toolName))
            return;

        switch (toolName)
        {
            case "change_gameobject_color":
                ApplyChangeColor(tokenObject);
                break;
            case "change_page":
                ApplyChangePage(tokenObject);
                break;
            case "get_user_pov":
                StartCoroutine(CaptureAndSendUserPov(tokenObject));
                break;
        }
    }

    private IEnumerator CaptureAndSendUserPov(JToken token)
    {
        var requestId = token["data"]?["request_id"]?.Value<string>();
        if (string.IsNullOrWhiteSpace(requestId))
        {
            Debug.LogWarning("[AgentToolExecutor] get_user_pov missing 'data.request_id'.");
            yield break;
        }

        if (_userPovFeed == null)
        {
            SendUserPovError(requestId, "camera_unavailable", "No raw camera feed is configured in this scene.");
            yield break;
        }

        if (_userPovFeed.State == XRCameraFeedState.Stopped)
            _userPovFeed.StartFeed();

        var deadline = Time.realtimeSinceStartup + userPovCaptureTimeoutSeconds;
        while (_userPovFeed.Texture == null)
        {
            if (IsTerminalCameraState(_userPovFeed.State))
            {
                SendUserPovError(requestId, GetCameraErrorCode(_userPovFeed.State), _userPovFeed.Status);
                yield break;
            }

            if (Time.realtimeSinceStartup >= deadline)
            {
                SendUserPovError(requestId, "capture_timeout", "The camera feed did not produce an image in time.");
                yield break;
            }

            yield return null;
        }

        if (!(_userPovFeed.Texture is Texture2D texture))
        {
            SendUserPovError(requestId, "unsupported_texture", "The raw camera feed is not a readable Texture2D.");
            yield break;
        }

        byte[] jpegBytes;
        try
        {
            jpegBytes = ImageConversion.EncodeToJPG(texture, userPovJpegQuality);
        }
        catch (Exception ex)
        {
            SendUserPovError(requestId, "encoding_failed", ex.Message);
            yield break;
        }

        if (jpegBytes == null || jpegBytes.Length == 0)
        {
            SendUserPovError(requestId, "encoding_failed", "Unity returned an empty JPEG image.");
            yield break;
        }

        if (_imageCaptureFeedback == null)
        {
            _imageCaptureFeedback = GetComponent<ImageCaptureFeedback>();
            if (_imageCaptureFeedback == null)
                _imageCaptureFeedback = gameObject.AddComponent<ImageCaptureFeedback>();
        }

        _imageCaptureFeedback.Show(jpegBytes);

        var image = new JObject
        {
            ["mime_type"] = "image/jpeg",
            ["base64"] = Convert.ToBase64String(jpegBytes),
            ["width"] = texture.width,
            ["height"] = texture.height,
            ["source"] = _userPovFeed.SourceId,
            ["captured_at_utc"] = DateTimeOffset.UtcNow.ToString("O"),
        };

        _ = ellmo.SendUnityToolResultAsync(
            "get_user_pov",
            requestId,
            true,
            new JObject { ["image"] = image });
    }

    private void SendUserPovError(string requestId, string code, string message)
    {
        Debug.LogWarning($"[AgentToolExecutor] get_user_pov failed: {message}");
        _ = ellmo.SendUnityToolResultAsync(
            "get_user_pov",
            requestId,
            false,
            new JObject
            {
                ["error"] = new JObject
                {
                    ["code"] = code,
                    ["message"] = message,
                },
            });
    }

    private static bool IsTerminalCameraState(XRCameraFeedState state)
    {
        return state == XRCameraFeedState.PermissionDenied ||
               state == XRCameraFeedState.Unsupported ||
               state == XRCameraFeedState.Error;
    }

    private static string GetCameraErrorCode(XRCameraFeedState state)
    {
        switch (state)
        {
            case XRCameraFeedState.PermissionDenied:
                return "permission_denied";
            case XRCameraFeedState.Unsupported:
                return "camera_unsupported";
            default:
                return "camera_error";
        }
    }

    private void ApplyChangeColor(JToken token)
    {
        if (colorRenderer == null)
        {
            Debug.LogWarning("[AgentToolExecutor] colorRenderer reference is missing; cannot apply change_gameobject_color.");
            return;
        }

        var colorToken = token["data"]?["color"];
        if (colorToken == null)
        {
            Debug.LogWarning("[AgentAffordance] change_gameobject_color missing 'data.color'.");
            return;
        }

        // The tool payload appears to be 0-255 per channel.
        if (!TryParseFloat(colorToken["r"], out var r) ||
            !TryParseFloat(colorToken["g"], out var g) ||
            !TryParseFloat(colorToken["b"], out var b))
        {
            Debug.LogWarning("[AgentAffordance] change_gameobject_color has invalid r/g/b values.");
            return;
        }

        r = Mathf.Clamp01(r / 255f);
        g = Mathf.Clamp01(g / 255f);
        b = Mathf.Clamp01(b / 255f);

        // Note: accessing .material creates an instance; that's fine for per-object color changes.
        colorRenderer.material.color = new Color(r, g, b, 1f);
    }

    private void ApplyChangePage(JToken token)
    {
        var data = token["data"];
        var requestId = data?["request_id"]?.Value<string>();
        if (string.IsNullOrWhiteSpace(requestId))
        {
            Debug.LogWarning("[AgentToolExecutor] change_page missing 'data.request_id'.");
            return;
        }
        if (pdfViewerUI == null || pdfViewerUI.GetTotalPages() == 0)
        {
            SendPageResult(requestId, false, "PDF unavailable; load the same document used for retrieval.");
            return;
        }
        try
        {
            var direction = data["direction"]?.Value<string>();
            if (string.Equals(direction, "next", StringComparison.OrdinalIgnoreCase))
                pdfViewerUI.NextPage();
            else if (string.Equals(direction, "previous", StringComparison.OrdinalIgnoreCase))
                pdfViewerUI.PreviousPage();
            else if (!string.IsNullOrEmpty(direction))
            {
                SendPageResult(requestId, false, "Unknown page direction.");
                return;
            }
            else if (!pdfViewerUI.GoToPageLabel(data["page_number"]?.Value<string>()))
            {
                SendPageResult(requestId, false, "Page label is missing, ambiguous, or could not be rendered.");
                return;
            }
            bool rendered = pdfViewerUI.IsCurrentPageRendered();
            SendPageResult(requestId, rendered, rendered ? null : "The requested PDF page could not be rendered.");
        }
        catch (Exception exception)
        {
            SendPageResult(requestId, false, exception.Message);
        }
    }

    private void SendPageResult(string requestId, bool succeeded, string error = null)
    {
        var payload = new JObject();
        if (succeeded)
        {
            payload["page_label"] = pdfViewerUI.GetCurrentPageLabel();
            payload["pdf_page"] = pdfViewerUI.GetCurrentPdfPage();
            payload["page_count"] = pdfViewerUI.GetTotalPages();
        }
        else
            payload["error"] = error;
        if (ellmo != null)
            _ = ellmo.SendUnityToolResultAsync("change_page", requestId, succeeded, payload);
    }

    private static bool TryParseFloat(JToken token, out float value)
    {
        value = 0f;
        if (token == null)
            return false;

        // Handle numbers or numeric strings.
        if (token.Type == JTokenType.Integer || token.Type == JTokenType.Float)
        {
            value = token.Value<float>();
            return true;
        }

        if (token.Type == JTokenType.String)
            return float.TryParse(token.Value<string>(), out value);

        return false;
    }
}
