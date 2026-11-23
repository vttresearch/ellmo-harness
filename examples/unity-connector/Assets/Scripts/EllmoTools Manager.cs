using System;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEngine;

/// <summary>
/// Unity-side manager for talking to the Python model API.
///
/// This component provides:
///   - Simple REST helpers (health, hosts, conversations)
///   - A WebSocket client for live chat streaming
///
/// It is intentionally self-contained so you can later split the
/// REST and WebSocket pieces into separate files (recommended for
/// larger projects) without changing the public surface.
/// </summary>
public class EllmoToolsManager : MonoBehaviour
{
    public readonly struct ServerSettings
    {
        public ServerSettings(string address, int restPort, int webSocketPort)
        {
            Address = address;
            RestPort = restPort;
            WebSocketPort = webSocketPort;
        }

        public string Address { get; }
        public int RestPort { get; }
        public int WebSocketPort { get; }
    }

    [Header("REST settings")]
    [Tooltip("Base URL of the FastAPI REST server, without trailing slash.")]
    [SerializeField] private string restBaseUrl = "http://127.0.0.1:8000";

    [Header("WebSocket settings")]
    [Tooltip("Base WS URL of the FastAPI WebSocket server, without the /{conversation_id} suffix.")]
    [SerializeField] private string wsBaseUrl = "ws://127.0.0.1:8001/ws/chat";

    [Tooltip("Logical conversation id. The backend currently accepts one connected client at a time.")]
    [SerializeField] private string conversationId = "unity-client";

    /// <summary>
    /// Fired when a log / info line is received from the backend.
    /// </summary>
    public event Action<JToken> OnLogMessage;

    /// <summary>
    /// Fired when the assistant sends a final message for a user turn.
    /// </summary>
    public event Action<JToken> OnAssistantMessage;

    private ToolsREST _restClient;
    private ToolsWebSocket _wsClient;

    private void Awake()
    {
        if (_restClient == null)
            _restClient = GetComponent<ToolsREST>();

        if (_wsClient == null)
            _wsClient = GetComponent<ToolsWebSocket>();

        if (_restClient != null)
        {
            _restClient.Initialize(
                restBaseUrl,
                token => OnLogMessage?.Invoke(token)
            );
        }

        if (_wsClient != null)
        {
            _wsClient.Initialize(
                wsBaseUrl,
                conversationId,
                token => OnLogMessage?.Invoke(token),
                token => OnAssistantMessage?.Invoke(token)
            );
        }
    }

    public ServerSettings GetServerSettings()
    {
        var address = "127.0.0.1";
        var restPort = 8000;
        var webSocketPort = 8001;

        if (Uri.TryCreate(restBaseUrl, UriKind.Absolute, out var restUri))
        {
            var host = FormatHostForDisplay(restUri);
            address = string.Equals(restUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)
                ? $"https://{host}"
                : host;
            restPort = restUri.Port;
        }

        if (Uri.TryCreate(wsBaseUrl, UriKind.Absolute, out var wsUri))
            webSocketPort = wsUri.Port;

        return new ServerSettings(address, restPort, webSocketPort);
    }

    public bool TryValidateServerSettings(
        string address,
        string restPortText,
        string webSocketPortText,
        out string error)
    {
        if (!int.TryParse(restPortText, out var restPort) || restPort < 1 || restPort > 65535)
        {
            error = "REST port must be between 1 and 65535.";
            return false;
        }

        if (!int.TryParse(webSocketPortText, out var webSocketPort) || webSocketPort < 1 || webSocketPort > 65535)
        {
            error = "WebSocket port must be between 1 and 65535.";
            return false;
        }

        return TryBuildServerUrls(address, restPort, webSocketPort, out _, out _, out error);
    }

    public async Task<bool> ApplyServerSettingsAsync(
        string address,
        int restPort,
        int webSocketPort)
    {
        if (!TryBuildServerUrls(
                address,
                restPort,
                webSocketPort,
                out var nextRestBaseUrl,
                out var nextWsBaseUrl,
                out var error))
        {
            OnLogMessage?.Invoke(JValue.CreateString($"[EllmoTools] Server settings rejected: {error}"));
            return false;
        }

        restBaseUrl = nextRestBaseUrl;
        wsBaseUrl = nextWsBaseUrl;

        _restClient?.Initialize(restBaseUrl, token => OnLogMessage?.Invoke(token));

        if (_wsClient == null)
        {
            OnLogMessage?.Invoke(JValue.CreateString("[EllmoTools] ToolsWebSocket component not assigned."));
            return false;
        }

        OnLogMessage?.Invoke(JValue.CreateString(
            $"[EllmoTools] Switching server to REST {restBaseUrl} and WebSocket {wsBaseUrl}."));
        return await _wsClient.ReconnectWebSocketAsync(wsBaseUrl, conversationId);
    }

    private static bool TryBuildServerUrls(
        string address,
        int restPort,
        int webSocketPort,
        out string nextRestBaseUrl,
        out string nextWsBaseUrl,
        out string error)
    {
        nextRestBaseUrl = null;
        nextWsBaseUrl = null;

        if (restPort < 1 || restPort > 65535 || webSocketPort < 1 || webSocketPort > 65535)
        {
            error = "Ports must be between 1 and 65535.";
            return false;
        }

        var trimmedAddress = address?.Trim();
        if (string.IsNullOrWhiteSpace(trimmedAddress))
        {
            error = "Server address is required.";
            return false;
        }

        var addressWithScheme = trimmedAddress.Contains("://")
            ? trimmedAddress
            : $"http://{trimmedAddress}";

        if (!Uri.TryCreate(addressWithScheme, UriKind.Absolute, out var serverUri) ||
            string.IsNullOrWhiteSpace(serverUri.Host))
        {
            error = "Enter an IP address or hostname, for example 127.0.0.1.";
            return false;
        }

        var isSecure = string.Equals(serverUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase);
        if (!isSecure && !string.Equals(serverUri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase))
        {
            error = "The address scheme must be http or https.";
            return false;
        }

        var restBuilder = new UriBuilder(isSecure ? Uri.UriSchemeHttps : Uri.UriSchemeHttp, serverUri.Host, restPort);
        var wsBuilder = new UriBuilder(isSecure ? "wss" : "ws", serverUri.Host, webSocketPort, "ws/chat");
        nextRestBaseUrl = restBuilder.Uri.GetLeftPart(UriPartial.Authority).TrimEnd('/');
        nextWsBaseUrl = wsBuilder.Uri.AbsoluteUri.TrimEnd('/');
        error = null;
        return true;
    }

    private static string FormatHostForDisplay(Uri uri)
    {
        return uri.HostNameType == UriHostNameType.IPv6 ? $"[{uri.Host}]" : uri.Host;
    }

    #region REST API helpers

    public void CheckHealth()
    {
        if (_restClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsREST component not assigned.");
            return;
        }

        _restClient.CheckHealth();
    }

    public void FetchMachines()
    {
        if (_restClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsREST component not assigned.");
            return;
        }

        _restClient.FetchMachines();
    }

    public void FetchConversations()
    {
        if (_restClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsREST component not assigned.");
            return;
        }

        _restClient.FetchConversations();
    }

    #endregion

    #region WebSocket chat

    public void ConnectWebSocket(string host = null, string model = null)
    {
        if (_wsClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsWebSocket component not assigned.");
            return;
        }

        _wsClient.ConnectWebSocket(host, model);
    }

    public Task SendConfigureAsync(string host, string model)
    {
        if (_wsClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsWebSocket component not assigned.");
            return Task.CompletedTask;
        }

        return _wsClient.SendConfigureAsync(host, model);
    }

    public void SendUserMessage(string text)
    {
        if (_wsClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsWebSocket component not assigned.");
            return;
        }

        _wsClient.SendUserMessage(text);
    }

    public Task SendUnityToolResultAsync(
        string toolName,
        string requestId,
        bool succeeded,
        JObject payload)
    {
        if (_wsClient == null)
        {
            OnLogMessage?.Invoke("[EllmoTools] ToolsWebSocket component not assigned.");
            return Task.CompletedTask;
        }

        return _wsClient.SendUnityToolResultAsync(toolName, requestId, succeeded, payload);
    }

    public void CloseWebSocket()
    {
        if (_wsClient == null)
            return;

        _wsClient.CloseWebSocket();
    }

    #endregion
}
