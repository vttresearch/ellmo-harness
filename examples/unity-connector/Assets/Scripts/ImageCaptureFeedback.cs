using System.Collections;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR.Management;

/// <summary>
/// Presents lightweight, head-visible feedback after a user-POV image is captured.
/// The UI and sound are generated at runtime so both desktop and XR scenes share
/// the behavior without requiring scene-specific references or imported assets.
/// </summary>
public sealed class ImageCaptureFeedback : MonoBehaviour
{
    private const float PreviewHoldSeconds = 2f;
    private const float PreviewFadeSeconds = 0.3f;
    private const float WorldSpaceCanvasScale = 0.001f;
    private const int PreviewSortingOrder = 1000;

    private static readonly Color FrameColor = new Color(0.94f, 0.95f, 0.97f, 1f);
    private static readonly Color FrameOutlineColor = new Color(0.025f, 0.035f, 0.05f, 0.95f);

    private AudioSource _audioSource;
    private GameObject _audioObject;
    private AudioClip _shutterClip;
    private GameObject _previewCanvas;
    private RectTransform _frameRect;
    private RawImage _previewImage;
    private CanvasGroup _canvasGroup;
    private Coroutine _hideRoutine;
    private Texture2D _previewTexture;

    /// <summary>
    /// Shows the exact JPEG sent to the backend, accompanied by a short shutter-like click.
    /// </summary>
    public void Show(byte[] jpegBytes)
    {
        PlayShutterSound();

        if (jpegBytes == null || jpegBytes.Length == 0)
            return;

        var capturedFrame = new Texture2D(2, 2, TextureFormat.RGBA32, false)
        {
            name = "Captured User POV Preview",
            filterMode = FilterMode.Bilinear,
            wrapMode = TextureWrapMode.Clamp,
        };

        if (!ImageConversion.LoadImage(capturedFrame, jpegBytes, false))
        {
            Destroy(capturedFrame);
            Debug.LogWarning("[ImageCaptureFeedback] Could not decode the captured image for its preview.", this);
            return;
        }

        EnsurePreviewUi();
        ReplacePreviewTexture(capturedFrame);
        ResizePreview(capturedFrame.width, capturedFrame.height);

        if (_hideRoutine != null)
            StopCoroutine(_hideRoutine);

        _previewCanvas.SetActive(true);
        _canvasGroup.alpha = 1f;
        _frameRect.localScale = Vector3.one;
        _hideRoutine = StartCoroutine(HidePreviewAfterDelay(capturedFrame));
    }

    private void PlayShutterSound()
    {
        if (_audioSource == null)
        {
            _audioObject = new GameObject("Image Capture Feedback Audio", typeof(AudioSource));
            _audioObject.transform.SetParent(transform, false);
            _audioSource = _audioObject.GetComponent<AudioSource>();
            _audioSource.playOnAwake = false;
            _audioSource.loop = false;
            _audioSource.spatialBlend = 0f;
            _audioSource.volume = 0.65f;
        }

        if (_shutterClip == null)
            _shutterClip = CreateShutterClip();

        _audioSource.PlayOneShot(_shutterClip);
    }

    private void EnsurePreviewUi()
    {
        if (_previewCanvas != null)
            return;

        Camera targetCamera = GetComponent<Camera>();
        if (targetCamera == null)
            targetCamera = Camera.main;

        bool useWorldSpace = IsXrActive(targetCamera);
        int uiLayer = LayerMask.NameToLayer("UI");
        if (uiLayer < 0)
            uiLayer = gameObject.layer;

        _previewCanvas = new GameObject(
            "Image Capture Feedback Canvas",
            typeof(RectTransform),
            typeof(Canvas),
            typeof(CanvasScaler));
        _previewCanvas.layer = uiLayer;

        var canvas = _previewCanvas.GetComponent<Canvas>();
        canvas.overrideSorting = true;
        canvas.sortingOrder = PreviewSortingOrder;

        var canvasRect = _previewCanvas.GetComponent<RectTransform>();
        var scaler = _previewCanvas.GetComponent<CanvasScaler>();

        if (useWorldSpace && targetCamera != null)
        {
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = targetCamera;
            canvasRect.SetParent(targetCamera.transform, false);
            canvasRect.localPosition = new Vector3(0f, -0.12f, 0.65f);
            canvasRect.localRotation = Quaternion.identity;
            canvasRect.localScale = Vector3.one * WorldSpaceCanvasScale;
            canvasRect.sizeDelta = new Vector2(420f, 360f);
            scaler.dynamicPixelsPerUnit = 2f;
        }
        else
        {
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            scaler.matchWidthOrHeight = 0.5f;
        }

        var frameObject = new GameObject(
            "Captured Image Frame",
            typeof(RectTransform),
            typeof(CanvasRenderer),
            typeof(Image),
            typeof(Outline),
            typeof(CanvasGroup));
        frameObject.layer = uiLayer;
        _frameRect = frameObject.GetComponent<RectTransform>();
        _frameRect.SetParent(canvasRect, false);
        _frameRect.anchorMin = new Vector2(0.5f, 0.5f);
        _frameRect.anchorMax = new Vector2(0.5f, 0.5f);
        _frameRect.pivot = new Vector2(0.5f, 0.5f);
        _frameRect.anchoredPosition = Vector2.zero;
        var frameImage = frameObject.GetComponent<Image>();
        frameImage.color = FrameColor;
        frameImage.raycastTarget = false;
        CuteUITheme.Apply(frameImage, CuteUITheme.PanelCornerRadius);

        var frameOutline = frameObject.GetComponent<Outline>();
        frameOutline.effectColor = FrameOutlineColor;
        frameOutline.effectDistance = new Vector2(4f, -4f);

        _canvasGroup = frameObject.GetComponent<CanvasGroup>();
        _canvasGroup.interactable = false;
        _canvasGroup.blocksRaycasts = false;

        var imageObject = new GameObject(
            "Captured Image",
            typeof(RectTransform),
            typeof(CanvasRenderer),
            typeof(RawImage));
        imageObject.layer = uiLayer;
        var imageRect = imageObject.GetComponent<RectTransform>();
        imageRect.SetParent(_frameRect, false);
        imageRect.anchorMin = new Vector2(0.5f, 0.5f);
        imageRect.anchorMax = new Vector2(0.5f, 0.5f);
        imageRect.pivot = new Vector2(0.5f, 0.5f);
        imageRect.anchoredPosition = Vector2.zero;
        _previewImage = imageObject.GetComponent<RawImage>();
        _previewImage.raycastTarget = false;

        _previewCanvas.SetActive(false);
    }

    private static bool IsXrActive(Camera targetCamera)
    {
        if (targetCamera != null && targetCamera.stereoEnabled)
            return true;

        XRGeneralSettings settings = XRGeneralSettings.Instance;
        return settings != null &&
               settings.Manager != null &&
               settings.Manager.activeLoader != null;
    }

    private void ResizePreview(int textureWidth, int textureHeight)
    {
        const float maxImageWidth = 340f;
        const float maxImageHeight = 280f;
        const float frameBorder = 12f;

        float aspect = textureHeight > 0 ? textureWidth / (float)textureHeight : 1f;
        float width = maxImageWidth;
        float height = width / aspect;
        if (height > maxImageHeight)
        {
            height = maxImageHeight;
            width = height * aspect;
        }

        _previewImage.rectTransform.sizeDelta = new Vector2(width, height);
        _frameRect.sizeDelta = new Vector2(width + frameBorder * 2f, height + frameBorder * 2f);
    }

    private void ReplacePreviewTexture(Texture2D capturedFrame)
    {
        if (_previewTexture != null)
            Destroy(_previewTexture);

        _previewTexture = capturedFrame;
        _previewImage.texture = capturedFrame;
    }

    private IEnumerator HidePreviewAfterDelay(Texture2D capturedFrame)
    {
        yield return new WaitForSecondsRealtime(PreviewHoldSeconds);

        float elapsed = 0f;
        while (elapsed < PreviewFadeSeconds)
        {
            elapsed += Time.unscaledDeltaTime;
            float progress = Mathf.Clamp01(elapsed / PreviewFadeSeconds);
            float easedProgress = progress * progress;
            _canvasGroup.alpha = 1f - easedProgress;
            _frameRect.localScale = Vector3.one * Mathf.Lerp(1f, 0.86f, easedProgress);
            yield return null;
        }

        if (_previewTexture == capturedFrame)
        {
            _previewImage.texture = null;
            Destroy(_previewTexture);
            _previewTexture = null;
            _previewCanvas.SetActive(false);
        }

        _hideRoutine = null;
    }

    private static AudioClip CreateShutterClip()
    {
        const int sampleRate = 44100;
        const float durationSeconds = 0.12f;
        int sampleCount = Mathf.CeilToInt(sampleRate * durationSeconds);
        var samples = new float[sampleCount];
        uint noiseState = 0x6d2b79f5u;

        for (int i = 0; i < sampleCount; i++)
        {
            float time = i / (float)sampleRate;
            noiseState = noiseState * 1664525u + 1013904223u;
            float noise = ((noiseState >> 8) / 8388607.5f) - 1f;

            float firstClick = Mathf.Exp(-time * 85f) *
                               (noise * 0.65f + Mathf.Sin(time * 2f * Mathf.PI * 2200f) * 0.35f);

            float secondTime = time - 0.045f;
            float secondClick = secondTime >= 0f
                ? Mathf.Exp(-secondTime * 55f) *
                  (noise * 0.45f + Mathf.Sin(secondTime * 2f * Mathf.PI * 950f) * 0.55f)
                : 0f;

            samples[i] = Mathf.Clamp(firstClick * 0.75f + secondClick * 0.55f, -1f, 1f);
        }

        var clip = AudioClip.Create("Generated Camera Shutter", sampleCount, 1, sampleRate, false);
        clip.SetData(samples, 0);
        return clip;
    }

    private void OnDestroy()
    {
        if (_previewTexture != null)
            Destroy(_previewTexture);
        if (_shutterClip != null)
            Destroy(_shutterClip);
        if (_audioObject != null)
            Destroy(_audioObject);
        if (_previewCanvas != null)
            Destroy(_previewCanvas);
    }
}
