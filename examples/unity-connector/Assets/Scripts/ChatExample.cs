using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public class ChatExample : MonoBehaviour
{

    [Header("Bubble Colors")]
    [SerializeField] private Color userBubbleColor = new Color(0.13349947f, 0.24889562f, 0.3773585f, 1f); // current prefab blue
    [SerializeField] private Color assistantBubbleColor = new Color(0.09f, 0.12f, 0.18f, 1f);              // darker/grey-ish blue

    [Header("Backend")]
    [SerializeField] private EllmoToolsManager ellmo;

    [Header("Optional Speech Client")]
    [Tooltip("Requires a separately supplied compatible speech service; none is bundled.")]
    [SerializeField] private bool enableVoiceControls = false;

    [Header("Chat UI")]
    [Tooltip("ScrollRect for assistant/user chat bubbles.")]
    [SerializeField] private ScrollRect chatScroll;
    [Tooltip("The Content RectTransform under the chat ScrollView.")]
    [SerializeField] private RectTransform chatContent;

    [Header("Log UI")]
    [Tooltip("ScrollRect for log bubbles.")]
    [SerializeField] private ScrollRect logScroll;
    [Tooltip("The Content RectTransform under the log ScrollView.")]
    [SerializeField] private RectTransform logContent;

    [Header("Bubble Prefab")]
    [Tooltip("Prefab for ONE message row (recommended: HorizontalLayoutGroup with spacers + bubble + TMP_Text).")]
    [SerializeField] private GameObject bubbleRowPrefab;

    [Header("Behavior")]
    [Tooltip("Auto-scroll chat to bottom only if the user is already near the bottom.")]
    [SerializeField] private bool autoScrollChat = true;

    [Tooltip("Auto-scroll logs to bottom only if the user is already near the bottom.")]
    [SerializeField] private bool autoScrollLogs = true;

    [Tooltip("How close to the bottom (0 = bottom, 1 = top) the scroll must be to auto-scroll.")]
    [Range(0f, 0.5f)]
    [SerializeField] private float autoScrollThreshold = 0.05f;

    [Header("Message Animation")]
    [Tooltip("Fade and gently scale new chat and log bubbles into view.")]
    [SerializeField] private bool animateNewBubbles = true;

    [Tooltip("Duration of the new-bubble entrance animation, in unscaled seconds.")]
    [Min(0f)]
    [SerializeField] private float bubbleAppearDuration = 0.18f;

    [Tooltip("Scale used at the start of the new-bubble entrance animation.")]
    [Range(0.8f, 1f)]
    [SerializeField] private float bubbleAppearStartScale = 0.96f;

    [Header("Log Collapsing")]
    [Tooltip("When enabled, long log entries start collapsed and can be expanded by clicking the bubble.")]
    [SerializeField] private bool enableCollapsibleLogs = true;

    [Tooltip("Collapse log entries that have at least this many lines.")]
    [SerializeField] private int logAutoCollapseLineLimit = 8;

    [Tooltip("Collapse log entries that have at least this many characters.")]
    [SerializeField] private int logAutoCollapseCharLimit = 500;

    [Tooltip("Max preview length for collapsed log summaries.")]
    [SerializeField] private int logSummaryMaxChars = 100;

    [Header("Bubble Sizing")]
    [Tooltip("Maximum width (in pixels) of the bubble, excluding padding. Text will wrap past this.")]
    [SerializeField] private float maxBubbleTextWidth = 520f;

    [Tooltip("Minimum width (in pixels) of the bubble, excluding padding.")]
    [SerializeField] private float minBubbleTextWidth = 120f;

    [Tooltip("Extra padding (in pixels) added around the text when sizing the bubble. X = horizontal, Y = vertical.")]
    [SerializeField] private Vector2 bubblePadding = new Vector2(40f, 24f);

    [Header("Input")]
    [SerializeField] private TMP_InputField inputField;

    [Header("Host Selector")]
    [Tooltip("Assign the in-scene TMP_Dropdown used to select a host.")]
    [SerializeField] private TMP_Dropdown machineDropdown;
    [SerializeField] private string loadingMachineLabel = "Loading hosts...";
    [SerializeField] private string noMachinesLabel = "(no hosts)";

    [Header("Model Selector")]
    [Tooltip("Assign the in-scene TMP_Dropdown used to select a model.")]
    [SerializeField] private TMP_Dropdown modelDropdown;
    [SerializeField] private string loadingModelLabel = "Loading models...";
    [SerializeField] private string noModelsLabel = "(no models)";

    [Header("Refresh")]
    [Tooltip("Optional refresh button. If not assigned, script tries to find 'Refresh models'.")]
    [SerializeField] private Button refreshModelsButton;

    [Header("Context Progress")]
    [Tooltip("Background container for the context progress bar.")]
    [SerializeField] private RectTransform contextProgressBar;
    [Tooltip("Back layer: total context usage (prompt + output).")]
    [SerializeField] private RectTransform totalFillRect;
    [Tooltip("Front layer: prompt-only context usage.")]
    [SerializeField] private RectTransform promptFillRect;
    [SerializeField] private TMP_Text progressLabel;

    private readonly List<string> _machineNames = new List<string>();
    private readonly Dictionary<string, List<string>> _machineToolModels = new Dictionary<string, List<string>>();
    private string _selectedMachine = string.Empty;
    private string _selectedModel = string.Empty;
    private bool _suppressMachineDropdownEvent;
    private bool _suppressModelDropdownEvent;
    private float _contextPromptRatio;
    private float _contextTotalRatio;
    private ServerSettingsPanel _serverSettingsPanel;
    private VoiceManager _voiceManager;
    private Button _voiceButton;
    private TMP_Text _voiceButtonLabel;
    private ColorBlock _voiceButtonDefaultColors;
    private readonly HashSet<ScrollRect> _pendingAutoScrolls = new HashSet<ScrollRect>();

    private static readonly Color ToolCallBubbleColor = new Color(0.075f, 0.18f, 0.29f, 1f);
    private static readonly Color ToolOutputBubbleColor = new Color(0.07f, 0.22f, 0.2f, 1f);

    private const string ToolCallAccent = "#82C7FF";
    private const string ToolOutputAccent = "#75E0B2";
    private const string ToolSectionColor = "#B8C8D8";
    private const string ToolHintColor = "#99A9BA";

    private void Start()
    {
        var uiCanvas = inputField != null ? inputField.GetComponentInParent<Canvas>() : null;
        CuteUITheme.ApplyToHierarchy(uiCanvas != null ? uiCanvas.transform : transform.root);

        if (ellmo == null)
        {
            Debug.LogError("[ChatExample] EllmoToolsManager reference is missing.");
            return;
        }

        // Hook backend events -> UI bubbles
        ellmo.OnLogMessage += HandleLogMessage;
        ellmo.OnAssistantMessage += HandleAssistantMessage;

        // TMP_InputField: OnSubmit fires when Enter is pressed (single-line) / submit action.
        if (inputField != null)
            inputField.onSubmit.AddListener(OnInputFieldSubmitted);

        InitializeMachineDropdown();
        InitializeModelDropdown();
        InitializeRefreshButton();
        if (enableVoiceControls)
            InitializeVoiceControls();
        InitializeServerSettingsPanel();
        InitializeContextProgressBar();

        // REST examples
        // ellmo.CheckHealth();
        // ellmo.FetchConversations();

        // Start websocket and fetch dynamic host list for selection.
        ellmo.ConnectWebSocket();
        ellmo.FetchMachines();
    }

    private void OnDestroy()
    {
        if (ellmo != null)
        {
            ellmo.OnLogMessage -= HandleLogMessage;
            ellmo.OnAssistantMessage -= HandleAssistantMessage;
        }

        if (inputField != null)
            inputField.onSubmit.RemoveListener(OnInputFieldSubmitted);

        if (machineDropdown != null)
            machineDropdown.onValueChanged.RemoveListener(OnMachineDropdownChanged);

        if (modelDropdown != null)
            modelDropdown.onValueChanged.RemoveListener(OnModelDropdownChanged);

        if (refreshModelsButton != null)
            refreshModelsButton.onClick.RemoveListener(OnRefreshModelsClicked);

        if (_voiceManager != null)
        {
            _voiceManager.TranscriptReady -= HandleVoiceTranscript;
            _voiceManager.StateChanged -= HandleVoiceStateChanged;
        }

        if (_voiceButton != null)
        {
            _voiceButton.onClick.RemoveListener(OnVoiceButtonClicked);
            Destroy(_voiceButton.gameObject);
        }
    }

    private void HandleLogMessage(JToken token)
    {
        Debug.Log("[LOG] " + token.ToString(Formatting.None));
        TryConsumeMachinesPayload(token);
        TryConsumeProgressStats(token);

        if (token is JObject tokenObject)
        {
            var eventType = tokenObject["type"]?.Value<string>();
            var agentKind = tokenObject["data"]?["kind"]?.Value<string>();
            if (string.Equals(eventType, "agent_message", StringComparison.Ordinal) &&
                string.Equals(agentKind, "tool_call", StringComparison.Ordinal) &&
                string.IsNullOrWhiteSpace(tokenObject["data"]?["content"]?.Value<string>()))
            {
                _voiceManager?.StartToolCallCue();
            }
            else if (string.Equals(eventType, "error", StringComparison.Ordinal) ||
                     string.Equals(agentKind, "error", StringComparison.Ordinal))
            {
                _voiceManager?.StopToolCallCue();
            }
        }

        if (TryBuildToolLogPresentation(token, out var collapsedToolText, out var expandedToolText, out var bubbleColor))
        {
            var initialToolText = enableCollapsibleLogs ? collapsedToolText : expandedToolText;
            var toolRow = AddBubble(logContent, logScroll, initialToolText, BubbleAlign.Log, allowAutoScroll: autoScrollLogs);
            SetBubbleColor(toolRow, bubbleColor);

            if (enableCollapsibleLogs)
                AttachLogCollapseToggle(toolRow, expandedToolText, collapsedToolText);

            return;
        }

        var formatted = FormatLogToken(token);
        if (string.IsNullOrWhiteSpace(formatted))
            return;

        var normalized = NormalizeLineEndings(formatted).TrimEnd('\n');
        var shouldCollapse = ShouldAutoCollapseLogEntry(normalized);
        var collapsedSummary = shouldCollapse ? BuildCollapsedLogSummary(normalized) : string.Empty;
        var expandedText = shouldCollapse ? BuildExpandedLogText(normalized) : normalized;
        var initialText = shouldCollapse ? collapsedSummary : normalized;

        var rowGO = AddBubble(logContent, logScroll, initialText, BubbleAlign.Log, allowAutoScroll: autoScrollLogs);
        if (shouldCollapse)
            AttachLogCollapseToggle(rowGO, expandedText, collapsedSummary);
    }

    private void HandleAssistantMessage(JToken token)
    {
        Debug.Log(DynamicPrettyPrinter.Print(token));
        TryConsumeProgressStats(token);
        var tokenObject = token as JObject;
        var msg = tokenObject?["data"]?["content"]?.Value<string>();

        Debug.Log("[ASSISTANT] " + msg);
        if (string.IsNullOrWhiteSpace(msg))
            return;

        AddBubble(chatContent, chatScroll, msg, BubbleAlign.Left, allowAutoScroll: autoScrollChat);
        _voiceManager?.Speak(msg);
    }

    /// <summary>
    /// Reads the TMP input field, sends it to the backend, and clears the field.
    /// Desktop keeps keyboard focus; XR dismisses its virtual keyboard.
    /// Called by the Send button and by Enter (onSubmit).
    /// </summary>
    private void OnInputFieldSubmitted(string _)
    {
#if UNITY_ANDROID
        // TMP also raises onSubmit when Android reports that a soft keyboard has
        // merely closed. Meta's X button uses that path; the affirmative IME
        // action arrives while the keyboard is still visible.
        var keyboard = inputField.touchScreenKeyboard;
        if (keyboard != null && keyboard.status != TouchScreenKeyboard.Status.Visible)
            return;
#endif

        SendFromInputField();
    }

    public void SendFromInputField()
    {
        if (ellmo == null)
            return;

        if (inputField == null)
        {
            Debug.LogWarning("[ChatExample] InputField reference is missing.");
            return;
        }

        var text = inputField.text;
        if (string.IsNullOrWhiteSpace(text))
            return;

        _voiceManager?.StopToolCallCue();
        _voiceManager?.StopSpeaking();

        // Show user's bubble immediately
        AddBubble(chatContent, chatScroll, text.Trim(), BubbleAlign.Right, allowAutoScroll: true);

        // Send to backend
        ellmo.SendUserMessage(text.Trim());

        // Keep desktop typing fast, but do not reactivate an XR input after its
        // virtual keyboard's Send key has deliberately closed it.
        inputField.text = string.Empty;
        var xrKeyboard = inputField.GetComponent<Ellmo.XR.XRCanvasKeyboard>();
        if (xrKeyboard != null)
        {
            xrKeyboard.Close();
            inputField.DeactivateInputField();
        }
        else
        {
            inputField.ActivateInputField();
        }
    }

    // Optional API if you still want to send messages from elsewhere.
    public void SendChat(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return;

        _voiceManager?.StopToolCallCue();
        _voiceManager?.StopSpeaking();
        AddBubble(chatContent, chatScroll, text.Trim(), BubbleAlign.Right, allowAutoScroll: true);

        ellmo.SendUserMessage(text.Trim());
    }

    private void InitializeVoiceControls()
    {
        _voiceManager = GetComponent<VoiceManager>();
        if (_voiceManager == null)
            _voiceManager = FindFirstObjectByType<VoiceManager>();

        if (_voiceManager == null)
        {
            Debug.LogWarning("[ChatExample] VoiceManager is missing; speech controls are disabled.");
            return;
        }

        var settings = ellmo.GetServerSettings();
        _voiceManager.ConfigureServerHost(settings.Address);
        _voiceManager.TranscriptReady += HandleVoiceTranscript;
        _voiceManager.StateChanged += HandleVoiceStateChanged;

        var sendButtonObject = GameObject.Find("Send Button");
        var sendButton = sendButtonObject != null ? sendButtonObject.GetComponent<Button>() : null;
        if (sendButton == null)
        {
            Debug.LogWarning("[ChatExample] Send Button was not found; speech is available only through VoiceManager.ToggleRecording().");
            return;
        }

        _voiceButton = Instantiate(sendButton, sendButton.transform.parent, false);
        _voiceButton.gameObject.name = "Voice Button";
        _voiceButton.onClick = new Button.ButtonClickedEvent();
        _voiceButton.onClick.AddListener(OnVoiceButtonClicked);
        _voiceButtonDefaultColors = _voiceButton.colors;

        var sendRect = sendButton.transform as RectTransform;
        var voiceRect = _voiceButton.transform as RectTransform;
        if (sendRect != null && voiceRect != null)
        {
            var spacing = 20f;
            voiceRect.anchoredPosition = sendRect.anchoredPosition +
                                         new Vector2(sendRect.rect.width + spacing, 0f);
            voiceRect.SetSiblingIndex(sendRect.GetSiblingIndex() + 1);
        }

        _voiceButtonLabel = _voiceButton.GetComponentInChildren<TMP_Text>(true);
        if (_voiceButtonLabel != null)
        {
            _voiceButtonLabel.enableAutoSizing = true;
            _voiceButtonLabel.fontSizeMin = 12f;
            _voiceButtonLabel.fontSizeMax = Mathf.Max(12f, _voiceButtonLabel.fontSize);
        }

        HandleVoiceStateChanged(VoiceManager.InteractionState.Idle, "Talk");

        var openXRHandUi = FindFirstObjectByType<Ellmo.XR.OpenXRHandUI>();
        if (openXRHandUi != null)
            openXRHandUi.RefreshRuntimeControls();
    }

    private void OnVoiceButtonClicked()
    {
        _voiceManager?.ToggleRecording();
    }

    private void HandleVoiceTranscript(string transcript)
    {
        if (string.IsNullOrWhiteSpace(transcript))
            return;

        Debug.Log($"[ChatExample] Voice transcript: {transcript}");
        SendChat(transcript);
    }

    private void HandleVoiceStateChanged(VoiceManager.InteractionState state, string status)
    {
        if (_voiceButton == null)
            return;

        _voiceButton.interactable = state != VoiceManager.InteractionState.RequestingPermission &&
                                    state != VoiceManager.InteractionState.Transcribing;

        if (_voiceButtonLabel != null)
            _voiceButtonLabel.text = string.IsNullOrWhiteSpace(status) ? "Talk" : status;

        var colors = _voiceButtonDefaultColors;
        if (state == VoiceManager.InteractionState.Recording)
        {
            var recordingColor = new Color(0.9f, 0.25f, 0.22f, 1f);
            colors.normalColor = recordingColor;
            colors.selectedColor = recordingColor;
            colors.highlightedColor = new Color(1f, 0.38f, 0.34f, 1f);
        }

        _voiceButton.colors = colors;
    }

    private void ConfigureCurrentSelection()
    {
        if (ellmo == null)
            return;

        if (string.IsNullOrWhiteSpace(_selectedMachine) && string.IsNullOrWhiteSpace(_selectedModel))
            return;

        _ = ellmo.SendConfigureAsync(_selectedMachine, _selectedModel);
    }

    public void RefreshModels()
    {
        if (ellmo == null)
            return;

        ShowModelDropdownLoading();
        ellmo.FetchMachines();
    }

    private void InitializeRefreshButton()
    {
        if (refreshModelsButton == null)
        {
            var refreshGO = GameObject.Find("Refresh models") ??
                            GameObject.Find("Refresh Models") ??
                            GameObject.Find("Refresh Models Button");
            if (refreshGO != null)
                refreshModelsButton = refreshGO.GetComponent<Button>();
        }

        if (refreshModelsButton == null)
            return;

        refreshModelsButton.onClick.RemoveListener(OnRefreshModelsClicked);
        refreshModelsButton.onClick.AddListener(OnRefreshModelsClicked);
    }

    private void InitializeServerSettingsPanel()
    {
        if (inputField == null || refreshModelsButton == null)
        {
            Debug.LogWarning("[ChatExample] Server settings UI needs the input field and a button template.");
            return;
        }

        var uiOwner = inputField.transform.parent;
        if (uiOwner == null)
            return;

        _serverSettingsPanel = uiOwner.GetComponent<ServerSettingsPanel>();
        if (_serverSettingsPanel == null)
            _serverSettingsPanel = uiOwner.gameObject.AddComponent<ServerSettingsPanel>();

        _serverSettingsPanel.Initialize(ellmo, inputField, refreshModelsButton, OnServerSettingsApplied);
    }

    private void OnServerSettingsApplied()
    {
        if (_voiceManager != null)
            _voiceManager.ConfigureServerHost(ellmo.GetServerSettings().Address);

        _machineNames.Clear();
        _machineToolModels.Clear();
        _selectedMachine = string.Empty;
        _selectedModel = string.Empty;
        ShowMachineDropdownLoading();
        ShowModelDropdownLoading();
        ellmo.FetchMachines();
    }

    private void OnRefreshModelsClicked()
    {
        RefreshModels();
    }

    private void InitializeContextProgressBar()
    {
        if (contextProgressBar == null)
        {
            var barGO = GameObject.Find("Context Progress Bar");
            if (barGO != null)
                contextProgressBar = barGO.GetComponent<RectTransform>();
        }

        if (totalFillRect == null)
        {
            var totalGO = GameObject.Find("Total Fill");
            if (totalGO != null)
                totalFillRect = totalGO.GetComponent<RectTransform>();
        }

        if (promptFillRect == null)
        {
            var promptGO = GameObject.Find("Prompt Fill");
            if (promptGO != null)
                promptFillRect = promptGO.GetComponent<RectTransform>();
        }

        if (progressLabel == null)
        {
            var labelGO = GameObject.Find("Progress Label");
            if (labelGO != null)
                progressLabel = labelGO.GetComponent<TMP_Text>();
        }

        SetContextProgress(0f, 0f);
    }

    private void TryConsumeProgressStats(JToken token)
    {
        var tokenObject = token as JObject;
        var stats = tokenObject?["data"]?["stats"];
        if (stats == null)
            return;

        var promptRatio = (float)(stats["context_fullness"]?.Value<double?>() ?? 0.0);
        var totalRatio = (float)(stats["context_fullness_with_output"]?.Value<double?>() ?? 0.0);
        SetContextProgress(promptRatio, totalRatio);
    }

    private void SetContextProgress(float promptRatio, float totalRatio)
    {
        _contextPromptRatio = Mathf.Clamp01(promptRatio);
        _contextTotalRatio = Mathf.Clamp01(totalRatio);
        ApplyContextProgressVisuals();
    }

    private void ApplyContextProgressVisuals()
    {
        var totalPct = Mathf.RoundToInt(_contextTotalRatio * 100f);
        var promptPct = Mathf.RoundToInt(_contextPromptRatio * 100f);

        if (progressLabel != null)
            progressLabel.text = $"Context: {promptPct}% | With output: {totalPct}%";

        var widthSource = contextProgressBar;
        if (widthSource == null && totalFillRect != null)
            widthSource = totalFillRect.parent as RectTransform;
        if (widthSource == null && promptFillRect != null)
            widthSource = promptFillRect.parent as RectTransform;

        if (widthSource == null)
            return;

        var barWidth = Mathf.Max(0f, widthSource.rect.width);
        ApplyFillWidth(totalFillRect, barWidth * _contextTotalRatio);
        ApplyFillWidth(promptFillRect, barWidth * _contextPromptRatio);
    }

    private static void ApplyFillWidth(RectTransform fillRect, float width)
    {
        if (fillRect == null)
            return;

        fillRect.anchorMin = new Vector2(0f, fillRect.anchorMin.y);
        fillRect.anchorMax = new Vector2(0f, fillRect.anchorMax.y);
        fillRect.pivot = new Vector2(0f, fillRect.pivot.y);
        fillRect.anchoredPosition = new Vector2(0f, fillRect.anchoredPosition.y);
        fillRect.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, width);
    }

    private void OnRectTransformDimensionsChange()
    {
        // Keep fill widths in sync when the bar resizes (window/canvas scale changes).
        ApplyContextProgressVisuals();
    }

    private void InitializeMachineDropdown()
    {
        if (machineDropdown == null)
        {
            var dropdownGO = GameObject.Find("Machine Dropdown");
            if (dropdownGO != null)
                machineDropdown = dropdownGO.GetComponent<TMP_Dropdown>();
        }

        if (machineDropdown == null)
        {
            Debug.LogWarning("[ChatExample] Machine dropdown not assigned.");
            return;
        }

        machineDropdown.onValueChanged.RemoveListener(OnMachineDropdownChanged);
        machineDropdown.onValueChanged.AddListener(OnMachineDropdownChanged);
        ShowMachineDropdownLoading();
    }

    private void InitializeModelDropdown()
    {
        if (modelDropdown == null)
        {
            var dropdownGO = GameObject.Find("Model Dropdown");
            if (dropdownGO != null)
                modelDropdown = dropdownGO.GetComponent<TMP_Dropdown>();
        }

        if (modelDropdown == null)
        {
            Debug.LogWarning("[ChatExample] Model dropdown not assigned.");
            return;
        }

        modelDropdown.onValueChanged.RemoveListener(OnModelDropdownChanged);
        modelDropdown.onValueChanged.AddListener(OnModelDropdownChanged);
        ShowModelDropdownLoading();
    }

    private void ShowMachineDropdownLoading()
    {
        if (machineDropdown == null)
            return;

        _suppressMachineDropdownEvent = true;
        machineDropdown.ClearOptions();
        machineDropdown.AddOptions(new List<string> { loadingMachineLabel });
        machineDropdown.value = 0;
        machineDropdown.interactable = false;
        machineDropdown.RefreshShownValue();
        _suppressMachineDropdownEvent = false;
    }

    private void ShowMachineDropdownNoMachines()
    {
        if (machineDropdown == null)
            return;

        _suppressMachineDropdownEvent = true;
        machineDropdown.ClearOptions();
        machineDropdown.AddOptions(new List<string> { noMachinesLabel });
        machineDropdown.value = 0;
        machineDropdown.interactable = false;
        machineDropdown.RefreshShownValue();
        _suppressMachineDropdownEvent = false;
    }

    private void ShowModelDropdownLoading()
    {
        if (modelDropdown == null)
            return;

        _suppressModelDropdownEvent = true;
        modelDropdown.ClearOptions();
        modelDropdown.AddOptions(new List<string> { loadingModelLabel });
        modelDropdown.value = 0;
        modelDropdown.interactable = false;
        modelDropdown.RefreshShownValue();
        _suppressModelDropdownEvent = false;
    }

    private void ShowModelDropdownNoModels()
    {
        if (modelDropdown == null)
            return;

        _suppressModelDropdownEvent = true;
        modelDropdown.ClearOptions();
        modelDropdown.AddOptions(new List<string> { noModelsLabel });
        modelDropdown.value = 0;
        modelDropdown.interactable = false;
        modelDropdown.RefreshShownValue();
        _suppressModelDropdownEvent = false;
    }

    private void ApplyMachineDropdownOptions()
    {
        if (machineDropdown == null)
            return;

        if (_machineNames.Count == 0)
        {
            ShowMachineDropdownNoMachines();
            return;
        }

        var selectedIndex = _machineNames.IndexOf(_selectedMachine);
        if (selectedIndex < 0)
            selectedIndex = 0;

        _suppressMachineDropdownEvent = true;
        machineDropdown.ClearOptions();
        machineDropdown.AddOptions(_machineNames);
        machineDropdown.interactable = true;
        machineDropdown.value = selectedIndex;
        machineDropdown.RefreshShownValue();
        _suppressMachineDropdownEvent = false;
    }

    private void UpdateModelDropdownForSelectedMachine()
    {
        var hasModels = _machineToolModels.TryGetValue(_selectedMachine, out var models) &&
                        models != null &&
                        models.Count > 0;

        if (!hasModels)
        {
            _selectedModel = string.Empty;
            ShowModelDropdownNoModels();
            return;
        }

        if (!models.Contains(_selectedModel))
            _selectedModel = models[0];

        if (modelDropdown == null)
            return;

        var selectedIndex = models.IndexOf(_selectedModel);
        if (selectedIndex < 0)
            selectedIndex = 0;

        _suppressModelDropdownEvent = true;
        modelDropdown.ClearOptions();
        modelDropdown.AddOptions(models);
        modelDropdown.interactable = true;
        modelDropdown.value = selectedIndex;
        modelDropdown.RefreshShownValue();
        _suppressModelDropdownEvent = false;
    }

    private void OnMachineDropdownChanged(int index)
    {
        if (_suppressMachineDropdownEvent)
            return;

        if (index < 0 || index >= _machineNames.Count)
            return;

        SetSelectedMachine(_machineNames[index], sendConfigure: true);
    }

    private void OnModelDropdownChanged(int index)
    {
        if (_suppressModelDropdownEvent)
            return;

        if (!_machineToolModels.TryGetValue(_selectedMachine, out var models) || models == null)
            return;

        if (index < 0 || index >= models.Count)
            return;

        SetSelectedModel(models[index], sendConfigure: true);
    }

    private void SetSelectedMachine(string machineName, bool sendConfigure)
    {
        _selectedMachine = machineName ?? string.Empty;
        UpdateModelDropdownForSelectedMachine();

        if (sendConfigure)
            ConfigureCurrentSelection();
    }

    private void SetSelectedModel(string modelName, bool sendConfigure)
    {
        _selectedModel = modelName ?? string.Empty;

        if (sendConfigure)
            ConfigureCurrentSelection();
    }

    private static List<string> ExtractStringList(JToken token)
    {
        var result = new List<string>();
        if (!(token is JArray arr))
            return result;

        foreach (var item in arr)
        {
            var value = item?.Value<string>();
            if (!string.IsNullOrWhiteSpace(value))
                result.Add(value);
        }

        return result;
    }

    private bool TryConsumeMachinesPayload(JToken token)
    {
        var tokenObject = token as JObject;
        if (tokenObject?["type"]?.Value<string>() != "hosts")
            return false;

        var machinesObject = tokenObject["data"]?["hosts"] as JObject;

        _machineNames.Clear();
        _machineToolModels.Clear();
        if (machinesObject != null)
        {
            foreach (var prop in machinesObject.Properties())
            {
                _machineNames.Add(prop.Name);
                var machineInfo = prop.Value as JObject;
                _machineToolModels[prop.Name] = ExtractStringList(machineInfo?["tool_models"]);
            }
        }

        if (_machineNames.Count == 0)
        {
            _selectedMachine = string.Empty;
            _selectedModel = string.Empty;
            ShowMachineDropdownNoMachines();
            ShowModelDropdownNoModels();
            return true;
        }

        var machineToUse = _machineNames.Contains(_selectedMachine) ? _selectedMachine : _machineNames[0];
        // This also retries the WebSocket if its initial connection failed or if
        // a newly selected server's REST endpoint responded before its socket did.
        SetSelectedMachine(machineToUse, sendConfigure: true);
        ApplyMachineDropdownOptions();
        return true;
    }

    private enum BubbleAlign
    {
        Left,
        Right,
        Center,
        Log
    }

    /// <summary>
    /// Instantiates a bubble row under the given content transform and optionally scrolls to bottom.
    ///
    /// Prefab expectations (recommended):
    /// - Root: HorizontalLayoutGroup
    /// - Children:
    ///   - LeftSpacer  (LayoutElement)
    ///   - Bubble      (Image) -> Text (TMP_Text) somewhere in subtree
    ///   - RightSpacer (LayoutElement)
    ///
    /// Alignment:
    /// - Left:  LeftSpacer.flexibleWidth = 0, RightSpacer.flexibleWidth = 1
    /// - Right: LeftSpacer.flexibleWidth = 1, RightSpacer.flexibleWidth = 0
    /// - Center: both = 1
    /// - Log: same row alignment as Left, while preserving the log bubble color
    /// </summary>
    private GameObject AddBubble(RectTransform content, ScrollRect scroll, string text, BubbleAlign align, bool allowAutoScroll)
    {
        if (content == null)
        {
            Debug.LogWarning("[ChatExample] Content transform is missing; cannot add bubble.");
            return null;
        }

        if (bubbleRowPrefab == null)
        {
            Debug.LogWarning("[ChatExample] bubbleRowPrefab is missing; cannot add bubble.");
            return null;
        }

        // Capture this before the new row changes the content height. Checking after
        // the layout rebuild makes a scroll that was at the bottom appear to have
        // moved away from it, which is especially visible for asynchronous replies.
        var shouldAutoScroll = ShouldAutoScroll(scroll, allowAutoScroll);

        var rowGO = Instantiate(bubbleRowPrefab, content);
        rowGO.name = $"BubbleRow_{align}_{DateTime.Now:HHmmssfff}";

        // Color the bubble background based on who sent the message
        var bubbleTf2 = rowGO.transform.Find("Bubble");
        if (bubbleTf2 != null)
        {
            var bubbleImg = bubbleTf2.GetComponent<Image>();
            if (bubbleImg != null)
            {
                CuteUITheme.Apply(bubbleImg, CuteUITheme.BubbleCornerRadius);

                if (align == BubbleAlign.Right)      bubbleImg.color = userBubbleColor;       // user
                else if (align == BubbleAlign.Left)  bubbleImg.color = assistantBubbleColor;  // bot
                // Center and Log leave the prefab color as-is.
            }
        }

        // Set the text (find first TMP_Text in the row)
        var tmp = rowGO.GetComponentInChildren<TMP_Text>(includeInactive: true);
        if (tmp != null)
        {
            tmp.text = text;
            FitBubbleToText(rowGO, tmp);
        }
        else
        {
            Debug.LogWarning("[ChatExample] No TMP_Text found under bubbleRowPrefab instance.");
        }

        // Force layout rebuild so VerticalLayoutGroup spacing and preferred heights are respected.
        var rowRect = rowGO.transform as RectTransform;
        if (rowRect != null)
            LayoutRebuilder.ForceRebuildLayoutImmediate(rowRect);
        LayoutRebuilder.ForceRebuildLayoutImmediate(content);

        // Align using spacers if they exist
        var layoutElements = rowGO.GetComponentsInChildren<LayoutElement>(includeInactive: true);
        LayoutElement leftSpacer = null;
        LayoutElement rightSpacer = null;

        foreach (var le in layoutElements)
        {
            // We prefer explicit names if you follow the recommended prefab structure.
            if (le.gameObject.name.Equals("LeftSpacer", StringComparison.OrdinalIgnoreCase))
                leftSpacer = le;
            else if (le.gameObject.name.Equals("RightSpacer", StringComparison.OrdinalIgnoreCase))
                rightSpacer = le;
        }

        // If not named, try fallback: first two LayoutElements we find.
        if (leftSpacer == null || rightSpacer == null)
        {
            if (layoutElements.Length >= 2)
            {
                leftSpacer ??= layoutElements[0];
                rightSpacer ??= layoutElements[layoutElements.Length - 1];
            }
        }

        if (leftSpacer != null && rightSpacer != null)
        {
            switch (align)
            {
                case BubbleAlign.Left:
                case BubbleAlign.Log:
                    leftSpacer.flexibleWidth = 0f;
                    rightSpacer.flexibleWidth = 1f;
                    break;
                case BubbleAlign.Right:
                    leftSpacer.flexibleWidth = 1f;
                    rightSpacer.flexibleWidth = 0f;
                    break;
                case BubbleAlign.Center:
                    leftSpacer.flexibleWidth = 1f;
                    rightSpacer.flexibleWidth = 1f;
                    break;
            }
        }

        if (shouldAutoScroll)
            ScheduleScrollToBottom(scroll);

        if (animateNewBubbles && rowRect != null)
            StartCoroutine(AnimateBubbleAppearance(rowRect));

        return rowGO;
    }

    private bool ShouldAutoScroll(ScrollRect scroll, bool allowAutoScroll)
    {
        if (!allowAutoScroll || scroll == null)
            return false;

        // Keep auto-scroll active while a previous bubble is waiting for its layout
        // pass. Tool calls can arrive in a burst before that pass has completed.
        if (_pendingAutoScrolls.Contains(scroll))
            return true;

        var viewport = scroll.viewport != null ? scroll.viewport : scroll.transform as RectTransform;
        var content = scroll.content;
        if (viewport == null || content == null)
            return true;

        // A ScrollRect with no overflow may report either end depending on its
        // anchors, but adding the first overflowing bubble should still stay pinned.
        if (content.rect.height <= viewport.rect.height + 0.5f)
            return true;

        return scroll.verticalNormalizedPosition <= autoScrollThreshold;
    }

    private void ScheduleScrollToBottom(ScrollRect scroll)
    {
        if (scroll == null || !_pendingAutoScrolls.Add(scroll))
            return;

        StartCoroutine(ScrollToBottomAfterLayout(scroll));
    }

    private System.Collections.IEnumerator AnimateBubbleAppearance(RectTransform rowRect)
    {
        if (rowRect == null)
            yield break;

        // Do not use ?? here: Unity's destroyed-object "fake null" semantics are
        // handled by == null, while the CLR null-coalescing operator is not.
        var canvasGroup = rowRect.GetComponent<CanvasGroup>();
        if (canvasGroup == null)
            canvasGroup = rowRect.gameObject.AddComponent<CanvasGroup>();

        if (canvasGroup == null)
            yield break;

        var targetScale = rowRect.localScale;
        var startScaleFactor = Mathf.Clamp(bubbleAppearStartScale, 0.8f, 1f);
        var startScale = new Vector3(
            targetScale.x * startScaleFactor,
            targetScale.y * startScaleFactor,
            targetScale.z);
        var duration = Mathf.Max(0f, bubbleAppearDuration);

        canvasGroup.alpha = duration > 0f ? 0f : 1f;
        rowRect.localScale = duration > 0f ? startScale : targetScale;

        if (duration <= 0f)
            yield break;

        var elapsed = 0f;
        while (elapsed < duration)
        {
            if (rowRect == null || canvasGroup == null)
                yield break;

            elapsed += Time.unscaledDeltaTime;
            var t = Mathf.Clamp01(elapsed / duration);
            var easedT = t * t * (3f - 2f * t);
            canvasGroup.alpha = easedT;
            rowRect.localScale = Vector3.LerpUnclamped(startScale, targetScale, easedT);
            yield return null;
        }

        if (rowRect != null && canvasGroup != null)
        {
            canvasGroup.alpha = 1f;
            rowRect.localScale = targetScale;
        }
    }

    private void FitBubbleToText(GameObject rowGO, TMP_Text tmp)
    {
        if (rowGO == null || tmp == null)
            return;

        var bubbleTf = rowGO.transform.Find("Bubble");
        var bubbleRect = bubbleTf != null ? bubbleTf.GetComponent<RectTransform>() : null;
        if (bubbleRect == null) return;

        // Ensure bubble has LayoutElement
        var bubbleLayout = bubbleRect.GetComponent<LayoutElement>() ?? bubbleRect.gameObject.AddComponent<LayoutElement>();

        // Make TMP behave like a top-left “label” that we size ourselves
        tmp.enableWordWrapping = true;
        tmp.overflowMode = TextOverflowModes.Overflow;
        tmp.alignment = TextAlignmentOptions.TopLeft;

        // Put the TMP rect in the top-left corner of the bubble (NOT centered)
        var tr = tmp.rectTransform;
        tr.anchorMin = new Vector2(0f, 1f);
        tr.anchorMax = new Vector2(0f, 1f);
        tr.pivot     = new Vector2(0f, 1f);

        // Decide max text width (excluding padding)
        tmp.ForceMeshUpdate();
        var unclampedWidth = tmp.preferredWidth;
        var targetTextWidth = Mathf.Clamp(unclampedWidth, minBubbleTextWidth, maxBubbleTextWidth);

        // Ask TMP what height it needs *at that width*
        // (This is the key part that avoids “wrong preferredHeight until next frame”.)
        var preferred = tmp.GetPreferredValues(tmp.text, targetTextWidth, 0f);
        var targetTextHeight = preferred.y;

        // Size the TMP rect to exactly fit the wrapped text
        tr.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, targetTextWidth);
        tr.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical,   targetTextHeight);

        // Position text inside bubble with padding (half padding each side)
        tr.anchoredPosition = new Vector2(bubblePadding.x * 0.5f, -bubblePadding.y * 0.5f);

        // Bubble size = text size + padding
        var bubbleWidth  = targetTextWidth  + bubblePadding.x;
        var bubbleHeight = targetTextHeight + bubblePadding.y;

        bubbleLayout.minWidth = bubbleLayout.preferredWidth = bubbleWidth;
        bubbleLayout.minHeight = bubbleLayout.preferredHeight = bubbleHeight;
        bubbleLayout.flexibleWidth = 0f;
        bubbleLayout.flexibleHeight = 0f;

        // Force layout to update so the row height expands immediately
        LayoutRebuilder.ForceRebuildLayoutImmediate(bubbleRect);
        LayoutRebuilder.ForceRebuildLayoutImmediate((RectTransform)rowGO.transform);
    }

    private string FormatLogToken(JToken token)
    {
        if (token == null)
            return string.Empty;

        var tokenObject = token as JObject;
        var type = tokenObject?["type"]?.Value<string>();
        if (string.IsNullOrEmpty(type))
            return token.Type == JTokenType.String ? token.Value<string>() : token.ToString(Formatting.None);

        switch (type)
        {
            case "health":
            case "conversations":
            case "hosts":
            case "configured":
                return type;
            case "agent_message":
                var kind = tokenObject["data"]?["kind"]?.Value<string>();
                if (kind == "tool_call")
                {
                    var result = "tool call: ";
                    var toolCalls = tokenObject["data"]?["log"]?["tool_calls"];
                    if (toolCalls == null)
                        return result + "(none)";

                    foreach (var toolCall in toolCalls)
                    {
                        result += toolCall["tool_name"] + " -> arguments: ";
                        foreach (var toolArg in toolCall["arguments"] ?? new JArray())
                        {
                            var value = ((JProperty)toolArg).Value.ToString();
                            value = value.Length <= 30 ? value : value[..30] + "...";
                            result += "(" + ((JProperty)toolArg).Name + ": " + value + ") ";
                        }

                        result += "\n";
                    }
                    return result;
                }
                if (kind == "tool_result")
                {
                    var toolName = tokenObject["data"]?["tool_name"]?.Value<string>() ?? string.Empty;
                    if (string.IsNullOrWhiteSpace(toolName))
                        toolName = "unknown_tool";
                    else
                        toolName = toolName.Trim();

                    var toolOutput = tokenObject["data"]?["tool_output"]?.ToString() ?? string.Empty;
                    toolOutput = toolOutput.Replace("\r\n", "\n").Replace("\r", "\n");
                    if (string.IsNullOrEmpty(toolOutput))
                        toolOutput = "(empty)";

                    var header = $"Tool output for '{toolName}'";
                    var toolArguments = tokenObject["data"]?["tool_arguments"] as JObject;
                    if (toolArguments != null && toolArguments.HasValues)
                    {
                        var argParts = new List<string>();
                        foreach (var arg in toolArguments.Properties())
                        {
                            var argValue = arg.Value?.Type == JTokenType.String
                                ? arg.Value.Value<string>()
                                : arg.Value?.ToString(Formatting.None);
                            argParts.Add($"{arg.Name}='{argValue}'");
                        }

                        if (argParts.Count > 0)
                            header += ", " + string.Join(", ", argParts);
                    }

                    return header + "\n" + toolOutput;
                }
                break;
            case "unity_tool":
                // Scene-affordance/tool execution is handled by AgentAffordance.
                // Here we only format a readable log line.
                var unityToolName = tokenObject["data"]?["tool_name"]?.Value<string>() ?? "<unknown_tool>";
                return $"unity_tool: {unityToolName}";
        }

        return "Unknown Message Type";
    }

    private static string NormalizeLineEndings(string text)
    {
        return (text ?? string.Empty).Replace("\r\n", "\n").Replace("\r", "\n");
    }

    private static bool TryBuildToolLogPresentation(
        JToken token,
        out string collapsedText,
        out string expandedText,
        out Color bubbleColor)
    {
        collapsedText = string.Empty;
        expandedText = string.Empty;
        bubbleColor = ToolCallBubbleColor;

        if (token is not JObject tokenObject ||
            !string.Equals(tokenObject["type"]?.Value<string>(), "agent_message", StringComparison.Ordinal))
            return false;

        var data = tokenObject["data"] as JObject;
        var kind = data?["kind"]?.Value<string>();
        if (string.Equals(kind, "tool_call", StringComparison.Ordinal))
        {
            var calls = CollectToolCalls(data);
            var totalArgumentCount = 0;
            var toolNames = new List<string>();

            foreach (var call in calls)
            {
                toolNames.Add(GetToolName(call));
                if (call?["arguments"] is JObject arguments)
                    totalArgumentCount += arguments.Count;
            }

            if (toolNames.Count == 0)
                toolNames.Add("Unknown tool");

            var callLabel = calls.Count == 1 ? "Tool call" : $"Tool calls ({calls.Count})";
            var toolNameSummary = TruncatePlainText(string.Join(", ", toolNames), 86);
            var argumentSummary = totalArgumentCount == 1
                ? "1 argument"
                : $"{totalArgumentCount} arguments";

            collapsedText =
                $"<color={ToolCallAccent}><b>{callLabel}:</b></color> <b>{EscapeRichText(toolNameSummary)}</b>\n" +
                $"<color={ToolHintColor}>{argumentSummary}  •  Click / tap to expand</color>";

            var expanded = new StringBuilder();
            expanded.Append("<color=").Append(ToolCallAccent).Append("><b>")
                .Append(callLabel).Append(":</b></color> <b>")
                .Append(EscapeRichText(toolNameSummary)).AppendLine("</b>");

            if (calls.Count == 0)
            {
                expanded.AppendLine("<color=#D9E2EC>No tool details were provided.</color>");
            }
            else
            {
                for (var index = 0; index < calls.Count; index++)
                {
                    var call = calls[index];
                    if (index > 0)
                        expanded.AppendLine();

                    if (calls.Count > 1)
                    {
                        expanded.Append("<color=").Append(ToolSectionColor).Append("><b>")
                            .Append(index + 1).Append(". ")
                            .Append(EscapeRichText(GetToolName(call))).AppendLine("</b></color>");
                    }

                    AppendArguments(expanded, call?["arguments"] as JObject);
                }
            }

            expanded.Append("\n<color=").Append(ToolHintColor).Append(">Click / tap to collapse</color>");
            expandedText = expanded.ToString();
            bubbleColor = ToolCallBubbleColor;
            return true;
        }

        if (!string.Equals(kind, "tool_result", StringComparison.Ordinal))
            return false;

        var toolName = data?["tool_name"]?.Value<string>();
        if (string.IsNullOrWhiteSpace(toolName))
            toolName = "Unknown tool";
        else
            toolName = toolName.Trim();

        var output = FormatToolValue(data?["tool_output"]);
        if (string.IsNullOrWhiteSpace(output))
            output = "(empty output)";

        var outputLineCount = Mathf.Max(1, NormalizeLineEndings(output).Split('\n').Length);
        var outputPreview = TruncatePlainText(GetFirstNonEmptyLine(output), 96);
        if (string.IsNullOrWhiteSpace(outputPreview))
            outputPreview = "(empty output)";

        var lineSummary = outputLineCount == 1 ? "1 line" : $"{outputLineCount} lines";
        collapsedText =
            $"<color={ToolOutputAccent}><b>Tool output:</b></color> <b>{EscapeRichText(toolName)}</b>\n" +
            $"{EscapeRichText(outputPreview)}\n" +
            $"<color={ToolHintColor}>{lineSummary}  •  Click / tap to expand</color>";

        var expandedOutput = new StringBuilder();
        expandedOutput.Append("<color=").Append(ToolOutputAccent)
            .Append("><b>Tool output:</b></color> <b>")
            .Append(EscapeRichText(toolName)).AppendLine("</b>");

        var toolArguments = data?["tool_arguments"] as JObject;
        if (toolArguments != null && toolArguments.HasValues)
        {
            expandedOutput.AppendLine();
            AppendArguments(expandedOutput, toolArguments);
        }

        expandedOutput.Append("\n<color=").Append(ToolSectionColor)
            .AppendLine("><b>Result</b></color>");
        expandedOutput.Append("<size=82%><mspace=0.62em>")
            .Append(EscapeRichText(output)).Append("</mspace></size>");
        expandedOutput.Append("\n<color=").Append(ToolHintColor).Append(">Click / tap to collapse</color>");

        expandedText = expandedOutput.ToString();
        bubbleColor = ToolOutputBubbleColor;
        return true;
    }

    private static List<JToken> CollectToolCalls(JObject data)
    {
        var result = new List<JToken>();
        var callsToken = data?["log"]?["tool_calls"] ?? data?["tool_calls"];

        if (callsToken is JArray callsArray)
        {
            foreach (var call in callsArray)
            {
                if (call != null)
                    result.Add(call);
            }
        }
        else if (callsToken is JObject singleCall)
        {
            result.Add(singleCall);
        }

        return result;
    }

    private static string GetToolName(JToken toolCall)
    {
        var toolName = toolCall?["tool_name"]?.Value<string>();
        return string.IsNullOrWhiteSpace(toolName) ? "Unknown tool" : toolName.Trim();
    }

    private static void AppendArguments(StringBuilder builder, JObject arguments)
    {
        builder.Append("<color=").Append(ToolSectionColor).AppendLine("><b>Arguments</b></color>");
        if (arguments == null || !arguments.HasValues)
        {
            builder.AppendLine("<color=#D9E2EC>(none)</color>");
            return;
        }

        var argumentIndex = 0;
        foreach (var argument in arguments.Properties())
        {
            var value = FormatToolValue(argument.Value, Formatting.None);
            if (argumentIndex++ > 0)
                builder.AppendLine();

            builder.Append("<color=").Append(ToolSectionColor).Append("><b>")
                .Append(EscapeRichText(argument.Name)).AppendLine("</b></color>");
            builder.Append("<size=90%>").Append(EscapeRichText(value)).AppendLine("</size>");
        }
    }

    private static string FormatToolValue(JToken value, Formatting jsonFormatting = Formatting.Indented)
    {
        if (value == null || value.Type == JTokenType.Null)
            return "null";

        if (value.Type != JTokenType.String)
            return value.ToString(jsonFormatting);

        var text = NormalizeLineEndings(value.Value<string>() ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(text))
            return string.Empty;

        var firstCharacter = text[0];
        if (firstCharacter != '{' && firstCharacter != '[')
            return text;

        try
        {
            return JToken.Parse(text).ToString(jsonFormatting);
        }
        catch (JsonReaderException)
        {
            return text;
        }
    }

    private static string EscapeRichText(string text)
    {
        return (text ?? string.Empty)
            .Replace("&", "&amp;")
            .Replace("<", "&lt;")
            .Replace(">", "&gt;");
    }

    private static string TruncatePlainText(string text, int maxCharacters)
    {
        var normalized = NormalizeLineEndings(text).Replace("\n", " ").Trim();
        if (normalized.Length <= maxCharacters)
            return normalized;

        return normalized[..Mathf.Max(1, maxCharacters - 3)] + "...";
    }

    private bool ShouldAutoCollapseLogEntry(string text)
    {
        if (!enableCollapsibleLogs)
            return false;

        var normalized = NormalizeLineEndings(text).Trim();
        if (string.IsNullOrEmpty(normalized))
            return false;

        if (IsToolCallSummary(normalized))
            return false;

        if (IsToolOutputEntry(normalized))
            return true;

        var lineLimit = Mathf.Max(1, logAutoCollapseLineLimit);
        var charLimit = Mathf.Max(1, logAutoCollapseCharLimit);
        var lineCount = normalized.Split('\n').Length;

        return lineCount >= lineLimit || normalized.Length >= charLimit;
    }

    private static bool IsToolOutputEntry(string text)
    {
        var firstLine = GetFirstNonEmptyLine(text);
        return firstLine.StartsWith("Tool output for ", StringComparison.Ordinal);
    }

    private static bool IsToolCallSummary(string text)
    {
        var firstLine = GetFirstNonEmptyLine(text);
        return firstLine.StartsWith("tool call:", StringComparison.OrdinalIgnoreCase) ||
               firstLine.StartsWith("Agent message:", StringComparison.OrdinalIgnoreCase);
    }

    private static string GetFirstNonEmptyLine(string text)
    {
        var lines = NormalizeLineEndings(text).Split('\n');
        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (!string.IsNullOrEmpty(line))
                return line;
        }

        return string.Empty;
    }

    private string BuildCollapsedLogSummary(string text)
    {
        var normalized = NormalizeLineEndings(text).Trim();
        if (string.IsNullOrEmpty(normalized))
            return "> (empty log entry)";

        var lines = normalized.Split('\n');
        var lineCount = Mathf.Max(1, lines.Length);
        var preview = string.Empty;

        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (string.IsNullOrEmpty(line))
                continue;
            if (line == "{" || line == "}" || line == "[" || line == "]" || line == "]," || line == "},")
                continue;

            preview = line.TrimEnd(',');
            break;
        }

        if (string.IsNullOrEmpty(preview))
            preview = GetFirstNonEmptyLine(normalized);
        if (string.IsNullOrEmpty(preview))
            preview = "(empty)";

        var maxSummaryChars = Mathf.Max(30, logSummaryMaxChars);
        if (preview.Length > maxSummaryChars)
            preview = preview[..(maxSummaryChars - 3)] + "...";

        if (lineCount > 1)
            return $"> {preview} ({lineCount} lines)";

        return $"> {preview}";
    }

    private string BuildExpandedLogText(string text)
    {
        var normalized = NormalizeLineEndings(text).Trim();
        if (string.IsNullOrEmpty(normalized))
            return "v (empty log entry)";

        return "v " + normalized;
    }

    private void AttachLogCollapseToggle(GameObject rowGO, string expandedText, string collapsedSummary)
    {
        if (rowGO == null)
            return;

        var bubbleTf = rowGO.transform.Find("Bubble");
        if (bubbleTf == null)
            return;

        var tmp = rowGO.GetComponentInChildren<TMP_Text>(includeInactive: true);
        if (tmp == null)
            return;

        var bubbleGO = bubbleTf.gameObject;
        var button = bubbleGO.GetComponent<Button>();
        if (button == null)
            button = bubbleGO.AddComponent<Button>();

        if (button == null)
            return;

        button.targetGraphic = bubbleGO.GetComponent<Image>();
        button.transition = Selectable.Transition.ColorTint;
        var colors = button.colors;
        colors.normalColor = Color.white;
        colors.highlightedColor = new Color(0.9f, 0.97f, 1f, 1f);
        colors.pressedColor = new Color(0.78f, 0.88f, 0.94f, 1f);
        colors.selectedColor = colors.highlightedColor;
        colors.fadeDuration = 0.08f;
        button.colors = colors;
        var navigation = button.navigation;
        navigation.mode = Navigation.Mode.None;
        button.navigation = navigation;
        button.onClick.RemoveAllListeners();

        var isCollapsed = true;
        button.onClick.AddListener(() =>
        {
            isCollapsed = !isCollapsed;
            tmp.text = isCollapsed ? collapsedSummary : expandedText;
            FitBubbleToText(rowGO, tmp);

            if (rowGO.transform is RectTransform rowRect)
                LayoutRebuilder.ForceRebuildLayoutImmediate(rowRect);

            if (logContent != null)
                LayoutRebuilder.ForceRebuildLayoutImmediate(logContent);
        });
    }

    private static void SetBubbleColor(GameObject rowGO, Color color)
    {
        var bubbleImage = rowGO != null ? rowGO.transform.Find("Bubble")?.GetComponent<Image>() : null;
        if (bubbleImage != null)
            bubbleImage.color = color;
    }

    private System.Collections.IEnumerator ScrollToBottomAfterLayout(ScrollRect scroll)
    {
        yield return null;

        if (scroll == null)
        {
            _pendingAutoScrolls.Remove(scroll);
            yield break;
        }

        Canvas.ForceUpdateCanvases();

        if (scroll.content != null)
            LayoutRebuilder.ForceRebuildLayoutImmediate(scroll.content);

        scroll.StopMovement();
        scroll.verticalNormalizedPosition = 0f;
        Canvas.ForceUpdateCanvases();
        _pendingAutoScrolls.Remove(scroll);
    }
}
