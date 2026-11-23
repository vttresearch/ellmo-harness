using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Builds one shared runtime settings panel for both the desktop and OpenXR scenes.
/// Controls are cloned from the active scene so fonts, button styling, raycasters,
/// and the OpenXR keyboard behavior stay consistent with the existing UI.
/// </summary>
public sealed class ServerSettingsPanel : MonoBehaviour
{
    private static readonly Color DialogColor = new Color(0.055f, 0.075f, 0.105f, 0.99f);
    private static readonly Color LabelColor = new Color(0.94f, 0.96f, 1f, 1f);
    private static readonly Color ErrorColor = new Color(1f, 0.48f, 0.45f, 1f);
    private static readonly Color SuccessColor = new Color(0.45f, 0.9f, 0.62f, 1f);

    private EllmoToolsManager _ellmo;
    private Action _onServerChanged;
    private TMP_InputField _addressInput;
    private TMP_InputField _restPortInput;
    private TMP_InputField _webSocketPortInput;
    private TMP_Text _statusLabel;
    private Button _settingsButton;
    private Button _applyButton;
    private Button _cancelButton;
    private GameObject _panelRoot;
    private bool _applying;

    public void Initialize(
        EllmoToolsManager ellmo,
        TMP_InputField inputTemplate,
        Button buttonTemplate,
        Action onServerChanged)
    {
        if (_panelRoot != null)
            return;

        _ellmo = ellmo;
        _onServerChanged = onServerChanged;

        var uiParent = inputTemplate != null ? inputTemplate.transform.parent as RectTransform : null;
        if (_ellmo == null || inputTemplate == null || buttonTemplate == null || uiParent == null)
        {
            Debug.LogWarning("[ServerSettingsPanel] Cannot build settings UI because a template reference is missing.");
            return;
        }

        BuildUi(uiParent, inputTemplate, buttonTemplate);
    }

    private void BuildUi(RectTransform uiParent, TMP_InputField inputTemplate, Button buttonTemplate)
    {
        _settingsButton = CloneButton(buttonTemplate, uiParent, "Server Settings Button", "Server settings");
        SetRect(_settingsButton.transform as RectTransform, new Vector2(1f, 1f), new Vector2(-125f, -40f), new Vector2(220f, 58f));
        _settingsButton.onClick.AddListener(Open);

        _panelRoot = new GameObject("Server Settings Overlay", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
        _panelRoot.layer = uiParent.gameObject.layer;
        var overlayRect = _panelRoot.GetComponent<RectTransform>();
        overlayRect.SetParent(uiParent, false);
        overlayRect.anchorMin = Vector2.zero;
        overlayRect.anchorMax = Vector2.one;
        overlayRect.offsetMin = Vector2.zero;
        overlayRect.offsetMax = Vector2.zero;
        _panelRoot.GetComponent<Image>().color = new Color(0f, 0f, 0f, 0.72f);

        var dialog = new GameObject("Server Settings Dialog", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
        dialog.layer = uiParent.gameObject.layer;
        var dialogRect = dialog.GetComponent<RectTransform>();
        dialogRect.SetParent(overlayRect, false);
        SetRect(dialogRect, new Vector2(0.5f, 0.5f), Vector2.zero, new Vector2(780f, 520f));
        var dialogImage = dialog.GetComponent<Image>();
        dialogImage.color = DialogColor;
        CuteUITheme.Apply(dialogImage, CuteUITheme.PanelCornerRadius);

        var font = inputTemplate.textComponent != null
            ? inputTemplate.textComponent.font
            : TMP_Settings.defaultFontAsset;

        CreateLabel(dialogRect, font, "Server Settings Title", "Server connection", 34f,
            new Vector2(0f, 214f), new Vector2(690f, 48f), TextAlignmentOptions.Center);
        CreateLabel(dialogRect, font, "Server Address Label", "Server IP or hostname", 25f,
            new Vector2(0f, 151f), new Vector2(660f, 36f), TextAlignmentOptions.Left);

        _addressInput = CloneInput(inputTemplate, dialogRect, "Server Address Input", "127.0.0.1 or https://server.example");
        SetRect(_addressInput.transform as RectTransform, new Vector2(0.5f, 0.5f), new Vector2(0f, 103f), new Vector2(660f, 58f));

        CreateLabel(dialogRect, font, "REST Port Label", "REST port", 25f,
            new Vector2(-172f, 47f), new Vector2(316f, 36f), TextAlignmentOptions.Left);
        CreateLabel(dialogRect, font, "WebSocket Port Label", "WebSocket port", 25f,
            new Vector2(172f, 47f), new Vector2(316f, 36f), TextAlignmentOptions.Left);

        _restPortInput = CloneInput(inputTemplate, dialogRect, "REST Port Input", "8000");
        SetRect(_restPortInput.transform as RectTransform, new Vector2(0.5f, 0.5f), new Vector2(-172f, -1f), new Vector2(316f, 58f));
        ConfigurePortInput(_restPortInput);

        _webSocketPortInput = CloneInput(inputTemplate, dialogRect, "WebSocket Port Input", "8001");
        SetRect(_webSocketPortInput.transform as RectTransform, new Vector2(0.5f, 0.5f), new Vector2(172f, -1f), new Vector2(316f, 58f));
        ConfigurePortInput(_webSocketPortInput);

        _statusLabel = CreateLabel(dialogRect, font, "Server Settings Status",
            "Changing the server reconnects REST and WebSocket together.", 21f,
            new Vector2(0f, -84f), new Vector2(680f, 70f), TextAlignmentOptions.Center);

        _cancelButton = CloneButton(buttonTemplate, dialogRect, "Cancel Server Settings Button", "Close");
        SetRect(_cancelButton.transform as RectTransform, new Vector2(0.5f, 0.5f), new Vector2(-120f, -194f), new Vector2(210f, 58f));
        _cancelButton.onClick.AddListener(Close);

        _applyButton = CloneButton(buttonTemplate, dialogRect, "Apply Server Settings Button", "Apply & reconnect");
        SetRect(_applyButton.transform as RectTransform, new Vector2(0.5f, 0.5f), new Vector2(120f, -194f), new Vector2(250f, 58f));
        _applyButton.onClick.AddListener(Apply);

        _settingsButton.transform.SetAsLastSibling();
        _panelRoot.transform.SetAsLastSibling();

        // OpenXRHandUI performs its initial scan in Awake, before this runtime
        // panel exists. Re-scan now so every cloned field gets a fresh keyboard
        // binding instead of relying on a copied runtime component state.
        var openXRHandUi = FindFirstObjectByType<Ellmo.XR.OpenXRHandUI>();
        if (openXRHandUi != null)
            openXRHandUi.RefreshRuntimeControls();

        _panelRoot.SetActive(false);
    }

    private void Open()
    {
        if (_panelRoot == null || _ellmo == null || _applying)
            return;

        var settings = _ellmo.GetServerSettings();
        _addressInput.text = settings.Address;
        _restPortInput.text = settings.RestPort.ToString();
        _webSocketPortInput.text = settings.WebSocketPort.ToString();
        SetStatus("Changing the server reconnects REST and WebSocket together.", LabelColor);
        _panelRoot.SetActive(true);
        _panelRoot.transform.SetAsLastSibling();
    }

    private void Close()
    {
        if (!_applying && _panelRoot != null)
            _panelRoot.SetActive(false);
    }

    private async void Apply()
    {
        if (_applying || _ellmo == null)
            return;

        if (!_ellmo.TryValidateServerSettings(
                _addressInput.text,
                _restPortInput.text,
                _webSocketPortInput.text,
                out var error))
        {
            SetStatus(error, ErrorColor);
            return;
        }

        var restPort = int.Parse(_restPortInput.text);
        var webSocketPort = int.Parse(_webSocketPortInput.text);
        _applying = true;
        SetButtonsInteractable(false);
        SetStatus("Applying settings and reconnecting...", LabelColor);

        var reconnectTask = _ellmo.ApplyServerSettingsAsync(_addressInput.text, restPort, webSocketPort);
        // REST can refresh while the socket connects. If /hosts wins that race,
        // its configuration request waits for or retries the same new socket.
        _onServerChanged?.Invoke();
        var connected = await reconnectTask;

        // The component can be destroyed while ConnectAsync is in flight during a scene change.
        if (this == null)
            return;

        SetStatus(
            connected
                ? "Connected. Host and model choices are refreshing."
                : "Saved. WebSocket retry will run when the host list arrives.",
            connected ? SuccessColor : ErrorColor);
        _applying = false;
        SetButtonsInteractable(true);
    }

    private void SetButtonsInteractable(bool interactable)
    {
        if (_applyButton != null)
            _applyButton.interactable = interactable;
        if (_cancelButton != null)
            _cancelButton.interactable = interactable;
    }

    private void SetStatus(string text, Color color)
    {
        if (_statusLabel == null)
            return;

        _statusLabel.text = text;
        _statusLabel.color = color;
    }

    private static TMP_InputField CloneInput(
        TMP_InputField template,
        RectTransform parent,
        string objectName,
        string placeholder)
    {
        var clone = Instantiate(template, parent, false);
        clone.gameObject.name = objectName;
        clone.text = string.Empty;
        clone.lineType = TMP_InputField.LineType.SingleLine;
        clone.onValueChanged.RemoveAllListeners();
        clone.onEndEdit.RemoveAllListeners();
        clone.onSubmit.RemoveAllListeners();

        if (clone.placeholder is TMP_Text placeholderText)
            placeholderText.text = placeholder;

        return clone;
    }

    private static void ConfigurePortInput(TMP_InputField input)
    {
        input.contentType = TMP_InputField.ContentType.IntegerNumber;
        input.characterLimit = 5;
        input.ForceLabelUpdate();
    }

    private static Button CloneButton(
        Button template,
        RectTransform parent,
        string objectName,
        string label)
    {
        var clone = Instantiate(template, parent, false);
        clone.gameObject.name = objectName;
        clone.onClick.RemoveAllListeners();

        var text = clone.GetComponentInChildren<TMP_Text>(true);
        if (text != null)
            text.text = label;

        return clone;
    }

    private static TMP_Text CreateLabel(
        RectTransform parent,
        TMP_FontAsset font,
        string objectName,
        string text,
        float fontSize,
        Vector2 position,
        Vector2 size,
        TextAlignmentOptions alignment)
    {
        var labelObject = new GameObject(objectName, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
        labelObject.layer = parent.gameObject.layer;
        var rect = labelObject.GetComponent<RectTransform>();
        rect.SetParent(parent, false);
        SetRect(rect, new Vector2(0.5f, 0.5f), position, size);

        var label = labelObject.GetComponent<TextMeshProUGUI>();
        label.font = font;
        label.fontSize = fontSize;
        label.color = LabelColor;
        label.alignment = alignment;
        label.textWrappingMode = TextWrappingModes.Normal;
        label.raycastTarget = false;
        label.text = text;
        return label;
    }

    private static void SetRect(RectTransform rect, Vector2 anchor, Vector2 position, Vector2 size)
    {
        if (rect == null)
            return;

        rect.anchorMin = anchor;
        rect.anchorMax = anchor;
        rect.pivot = new Vector2(0.5f, 0.5f);
        rect.anchoredPosition = position;
        rect.sizeDelta = size;
        rect.localScale = Vector3.one;
    }

    private void OnDestroy()
    {
        if (_settingsButton != null)
            Destroy(_settingsButton.gameObject);
        if (_panelRoot != null)
            Destroy(_panelRoot);
    }
}
