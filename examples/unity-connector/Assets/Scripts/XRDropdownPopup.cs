using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Ellmo.XR
{
    /// <summary>
    /// Replaces TMP's separate-canvas dropdown popup with a list that stays on
    /// the main XR canvas. Its options use the same Button path as the working UI.
    /// </summary>
    [DisallowMultipleComponent]
    [RequireComponent(typeof(TMP_Dropdown))]
    public sealed class XRDropdownPopup : MonoBehaviour, IPointerClickHandler, ISubmitHandler
    {
        const float k_OptionHeight = 56f;
        const float k_PopupPadding = 6f;
        const int k_MaxVisibleOptions = 8;

        static readonly Color s_BackgroundColor = new(0.045f, 0.06f, 0.085f, 0.98f);
        static readonly Color s_OptionColor = new(0.11f, 0.15f, 0.2f, 1f);
        static readonly Color s_SelectedColor = new(0.12f, 0.38f, 0.55f, 1f);

        TMP_Dropdown m_Dropdown;
        Canvas m_RootCanvas;
        GameObject m_Blocker;
        GameObject m_Popup;

        void Awake()
        {
            m_Dropdown = GetComponent<TMP_Dropdown>();
            Canvas canvas = GetComponentInParent<Canvas>();
            m_RootCanvas = canvas != null ? canvas.rootCanvas : null;

            // TMP's popup is generated on separate canvases and does not sort
            // reliably against its blocker for tracked-device raycasts.
            m_Dropdown.enabled = false;
        }

        public void OnPointerClick(PointerEventData eventData)
        {
            if (eventData.button != PointerEventData.InputButton.Left)
                return;

            TogglePopup();
            eventData.Use();
        }

        public void OnSubmit(BaseEventData eventData)
        {
            TogglePopup();
            eventData.Use();
        }

        void TogglePopup()
        {
            if (m_Popup != null)
            {
                Close();
                return;
            }

            if (!m_Dropdown.interactable || !gameObject.activeInHierarchy || m_Dropdown.options.Count == 0)
                return;

            Open();
        }

        void Open()
        {
            if (m_RootCanvas == null)
                return;

            RectTransform canvasRect = m_RootCanvas.transform as RectTransform;
            RectTransform dropdownRect = transform as RectTransform;
            if (canvasRect == null || dropdownRect == null)
                return;

            Close();
            m_Blocker = CreateBlocker(canvasRect);
            m_Popup = CreatePopup(canvasRect, dropdownRect);
            Debug.Log($"[Ellmo OpenXR] Opened button-based dropdown '{name}' with {m_Dropdown.options.Count} options.", this);
        }

        GameObject CreateBlocker(RectTransform canvasRect)
        {
            GameObject blocker = new($"{name} XR Dropdown Blocker");
            blocker.layer = gameObject.layer;
            RectTransform rect = blocker.AddComponent<RectTransform>();
            rect.SetParent(canvasRect, false);
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            Image image = blocker.AddComponent<Image>();
            image.color = Color.clear;
            Button button = blocker.AddComponent<Button>();
            button.transition = Selectable.Transition.None;
            button.onClick.AddListener(Close);
            return blocker;
        }

        GameObject CreatePopup(RectTransform canvasRect, RectTransform dropdownRect)
        {
            int visibleOptions = Mathf.Min(k_MaxVisibleOptions, m_Dropdown.options.Count);
            float popupWidth = Mathf.Max(220f, dropdownRect.rect.width);
            float popupHeight = visibleOptions * k_OptionHeight + k_PopupPadding * 2f;

            GameObject popup = new($"{name} XR Dropdown Options");
            popup.layer = gameObject.layer;
            RectTransform popupRect = popup.AddComponent<RectTransform>();
            popupRect.SetParent(canvasRect, false);
            popupRect.anchorMin = Vector2.zero;
            popupRect.anchorMax = Vector2.zero;
            popupRect.sizeDelta = new Vector2(popupWidth, popupHeight);

            Vector3[] corners = new Vector3[4];
            dropdownRect.GetWorldCorners(corners);
            Vector3 below = canvasRect.InverseTransformPoint(corners[0]);
            Vector3 above = canvasRect.InverseTransformPoint(corners[1]);
            bool openAbove = below.y - popupHeight < canvasRect.rect.yMin;
            popupRect.pivot = openAbove ? new Vector2(0f, 0f) : new Vector2(0f, 1f);

            Vector3 target = openAbove ? above : below;
            target.x = Mathf.Clamp(target.x, canvasRect.rect.xMin, canvasRect.rect.xMax - popupWidth);
            if (openAbove)
                target.y = Mathf.Min(target.y, canvasRect.rect.yMax - popupHeight);
            else
                target.y = Mathf.Max(target.y, canvasRect.rect.yMin + popupHeight);
            popupRect.anchoredPosition = new Vector2(
                target.x - canvasRect.rect.xMin,
                target.y - canvasRect.rect.yMin);

            Image background = popup.AddComponent<Image>();
            background.color = s_BackgroundColor;
            CuteUITheme.Apply(background, CuteUITheme.PanelCornerRadius);

            GameObject viewportObject = new("Viewport");
            viewportObject.layer = gameObject.layer;
            RectTransform viewport = viewportObject.AddComponent<RectTransform>();
            viewport.SetParent(popupRect, false);
            viewport.anchorMin = Vector2.zero;
            viewport.anchorMax = Vector2.one;
            viewport.offsetMin = Vector2.one * k_PopupPadding;
            viewport.offsetMax = Vector2.one * -k_PopupPadding;
            viewportObject.AddComponent<RectMask2D>();

            GameObject contentObject = new("Options");
            contentObject.layer = gameObject.layer;
            RectTransform content = contentObject.AddComponent<RectTransform>();
            content.SetParent(viewport, false);
            content.anchorMin = new Vector2(0f, 1f);
            content.anchorMax = new Vector2(1f, 1f);
            content.pivot = new Vector2(0.5f, 1f);
            content.anchoredPosition = Vector2.zero;
            content.sizeDelta = new Vector2(0f, m_Dropdown.options.Count * k_OptionHeight);

            ScrollRect scrollRect = popup.AddComponent<ScrollRect>();
            scrollRect.viewport = viewport;
            scrollRect.content = content;
            scrollRect.horizontal = false;
            scrollRect.vertical = true;
            scrollRect.movementType = ScrollRect.MovementType.Clamped;
            scrollRect.scrollSensitivity = k_OptionHeight;

            TMP_FontAsset font = m_Dropdown.itemText != null
                ? m_Dropdown.itemText.font
                : m_Dropdown.captionText != null
                    ? m_Dropdown.captionText.font
                    : TMP_Settings.defaultFontAsset;

            for (int i = 0; i < m_Dropdown.options.Count; ++i)
                CreateOption(content, font, i);

            scrollRect.verticalNormalizedPosition = 1f;
            return popup;
        }

        void CreateOption(RectTransform content, TMP_FontAsset font, int optionIndex)
        {
            GameObject option = new($"Option {optionIndex}");
            option.layer = gameObject.layer;
            RectTransform rect = option.AddComponent<RectTransform>();
            rect.SetParent(content, false);
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(1f, 1f);
            rect.pivot = new Vector2(0.5f, 1f);
            rect.anchoredPosition = new Vector2(0f, -optionIndex * k_OptionHeight);
            rect.sizeDelta = new Vector2(0f, k_OptionHeight - 2f);

            Image image = option.AddComponent<Image>();
            image.color = optionIndex == m_Dropdown.value ? s_SelectedColor : s_OptionColor;
            CuteUITheme.Apply(image, CuteUITheme.ControlCornerRadius);
            Button button = option.AddComponent<Button>();
            button.targetGraphic = image;
            ColorBlock colors = button.colors;
            colors.normalColor = image.color;
            colors.highlightedColor = Color.Lerp(image.color, Color.white, 0.12f);
            colors.pressedColor = Color.Lerp(image.color, Color.black, 0.18f);
            colors.selectedColor = colors.highlightedColor;
            button.colors = colors;

            int selectedIndex = optionIndex;
            button.onClick.AddListener(() => SelectOption(selectedIndex));

            GameObject labelObject = new("Label");
            labelObject.layer = gameObject.layer;
            RectTransform labelRect = labelObject.AddComponent<RectTransform>();
            labelRect.SetParent(rect, false);
            labelRect.anchorMin = Vector2.zero;
            labelRect.anchorMax = Vector2.one;
            labelRect.offsetMin = new Vector2(16f, 2f);
            labelRect.offsetMax = new Vector2(-12f, -2f);

            TextMeshProUGUI label = labelObject.AddComponent<TextMeshProUGUI>();
            label.font = font;
            label.fontSize = 30f;
            label.color = Color.white;
            label.alignment = TextAlignmentOptions.MidlineLeft;
            label.text = m_Dropdown.options[optionIndex].text;
            label.raycastTarget = false;
        }

        void SelectOption(int optionIndex)
        {
            Close();
            m_Dropdown.value = optionIndex;
            m_Dropdown.RefreshShownValue();
            Debug.Log($"[Ellmo OpenXR] Selected option {optionIndex} on '{name}'.", this);
        }

        public void Close()
        {
            if (m_Popup != null)
                Destroy(m_Popup);
            if (m_Blocker != null)
                Destroy(m_Blocker);
            m_Popup = null;
            m_Blocker = null;
        }

        void OnDisable() => Close();
        void OnDestroy() => Close();
    }
}
