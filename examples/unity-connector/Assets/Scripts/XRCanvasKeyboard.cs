using System;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Ellmo.XR
{
    /// <summary>
    /// Uses the platform's native keyboard when one is available and provides a
    /// deterministic in-world fallback for desktop OpenXR runtimes.
    /// </summary>
    [DisallowMultipleComponent]
    [RequireComponent(typeof(TMP_InputField))]
    public sealed class XRCanvasKeyboard : MonoBehaviour, IPointerClickHandler, ISelectHandler
    {
        readonly struct KeyDefinition
        {
            public KeyDefinition(string label, string value, float width = 1f)
            {
                Label = label;
                Value = value;
                Width = width;
            }

            public string Label { get; }
            public string Value { get; }
            public float Width { get; }
        }

        const string k_Shift = "{shift}";
        const string k_Backspace = "{backspace}";
        const string k_Space = "{space}";
        const string k_Cancel = "{cancel}";
        const string k_Close = "{close}";
        const string k_Submit = "{submit}";
        const float k_KeyboardWidth = 940f;
        const float k_KeyboardHeight = 330f;
        const float k_Padding = 12f;
        const float k_KeyGap = 6f;
        const float k_RowGap = 6f;
        const float k_KeyHeight = 55f;

        static readonly KeyDefinition[][] s_Rows =
        {
            new[]
            {
                new KeyDefinition("1", "1"), new KeyDefinition("2", "2"), new KeyDefinition("3", "3"),
                new KeyDefinition("4", "4"), new KeyDefinition("5", "5"), new KeyDefinition("6", "6"),
                new KeyDefinition("7", "7"), new KeyDefinition("8", "8"), new KeyDefinition("9", "9"),
                new KeyDefinition("0", "0"), new KeyDefinition("-", "-"),
                new KeyDefinition("Back", k_Backspace, 1.8f),
            },
            new[]
            {
                new KeyDefinition("q", "q"), new KeyDefinition("w", "w"), new KeyDefinition("e", "e"),
                new KeyDefinition("r", "r"), new KeyDefinition("t", "t"), new KeyDefinition("y", "y"),
                new KeyDefinition("u", "u"), new KeyDefinition("i", "i"), new KeyDefinition("o", "o"),
                new KeyDefinition("p", "p"),
            },
            new[]
            {
                new KeyDefinition("a", "a"), new KeyDefinition("s", "s"), new KeyDefinition("d", "d"),
                new KeyDefinition("f", "f"), new KeyDefinition("g", "g"), new KeyDefinition("h", "h"),
                new KeyDefinition("j", "j"), new KeyDefinition("k", "k"), new KeyDefinition("l", "l"),
                new KeyDefinition("/", "/"),
            },
            new[]
            {
                new KeyDefinition("Shift", k_Shift, 1.6f), new KeyDefinition("z", "z"),
                new KeyDefinition("x", "x"), new KeyDefinition("c", "c"), new KeyDefinition("v", "v"),
                new KeyDefinition("b", "b"), new KeyDefinition("n", "n"), new KeyDefinition("m", "m"),
                new KeyDefinition(".", "."), new KeyDefinition(",", ","), new KeyDefinition("?", "?"),
            },
            new[]
            {
                new KeyDefinition("Cancel", k_Cancel, 1.7f), new KeyDefinition("Space", k_Space, 5.5f),
                new KeyDefinition("Close", k_Close, 1.7f), new KeyDefinition("Send", k_Submit, 1.7f),
            },
        };

        static readonly Color s_BackgroundColor = new(0.035f, 0.05f, 0.075f, 0.98f);
        static readonly Color s_KeyColor = new(0.12f, 0.17f, 0.23f, 1f);
        static readonly Color s_ActionKeyColor = new(0.1f, 0.32f, 0.45f, 1f);

        readonly List<(TMP_Text label, string lowerCase)> m_LetterLabels = new();
        TMP_InputField m_InputField;
        Canvas m_RootCanvas;
        GameObject m_Keyboard;
        string m_OriginalText;
        bool m_Shifted;
        bool m_UseSystemKeyboard;

        void Awake()
        {
            RefreshBinding();
        }

        /// <summary>
        /// Rebinds this helper after a TMP input is cloned or reparented at runtime.
        /// </summary>
        public void RefreshBinding()
        {
            m_InputField = GetComponent<TMP_InputField>();
            Canvas canvas = GetComponentInParent<Canvas>();
            m_RootCanvas = canvas != null ? canvas.rootCanvas : null;
            m_UseSystemKeyboard = TouchScreenKeyboard.isSupported;
            if (m_InputField == null)
                return;

            m_InputField.shouldHideSoftKeyboard = !m_UseSystemKeyboard;

            // Keyboard buttons temporarily take EventSystem focus. Selecting the
            // full field again on every refocus made each key replace all text.
            m_InputField.onFocusSelectAll = false;
        }

        public void OnPointerClick(PointerEventData eventData)
        {
            if (eventData.button == PointerEventData.InputButton.Left)
                Show();
        }

        public void OnSelect(BaseEventData eventData) => Show();

        public void Show()
        {
            if (m_InputField == null || m_RootCanvas == null)
                RefreshBinding();

            if (m_InputField == null)
                return;

            // TMP_InputField opens and synchronizes the native OS keyboard. On
            // Quest, the manifest opts this into Meta's system keyboard overlay.
            if (m_UseSystemKeyboard)
            {
                m_InputField.shouldHideSoftKeyboard = false;
                if (!m_InputField.isFocused)
                    m_InputField.ActivateInputField();
                return;
            }

            if (m_Keyboard != null)
            {
                m_Keyboard.transform.SetAsLastSibling();
                return;
            }

            if (m_RootCanvas == null || !m_InputField.interactable)
                return;

            RectTransform canvasRect = m_RootCanvas.transform as RectTransform;
            RectTransform inputRect = transform as RectTransform;
            if (canvasRect == null || inputRect == null)
                return;

            m_OriginalText = m_InputField.text;
            m_Shifted = false;
            m_LetterLabels.Clear();
            m_Keyboard = BuildKeyboard(canvasRect, inputRect);
            Debug.Log("[Ellmo OpenXR] Opened in-world button keyboard.", this);
        }

        GameObject BuildKeyboard(RectTransform canvasRect, RectTransform inputRect)
        {
            float width = Mathf.Min(k_KeyboardWidth, canvasRect.rect.width - 20f);
            GameObject keyboard = new("XR In-World Keyboard");
            keyboard.layer = gameObject.layer;
            RectTransform keyboardRect = keyboard.AddComponent<RectTransform>();
            keyboardRect.SetParent(canvasRect, false);
            keyboardRect.anchorMin = Vector2.zero;
            keyboardRect.anchorMax = Vector2.zero;
            keyboardRect.pivot = new Vector2(0.5f, 0f);
            keyboardRect.sizeDelta = new Vector2(width, k_KeyboardHeight);

            Vector3[] corners = new Vector3[4];
            inputRect.GetWorldCorners(corners);
            Vector3 inputTopCenter = canvasRect.InverseTransformPoint((corners[1] + corners[2]) * 0.5f);
            float x = Mathf.Clamp(
                inputTopCenter.x,
                canvasRect.rect.xMin + width * 0.5f,
                canvasRect.rect.xMax - width * 0.5f);
            float y = Mathf.Min(inputTopCenter.y + 10f, canvasRect.rect.yMax - k_KeyboardHeight);
            keyboardRect.anchoredPosition = new Vector2(
                x - canvasRect.rect.xMin,
                y - canvasRect.rect.yMin);

            Image background = keyboard.AddComponent<Image>();
            background.color = s_BackgroundColor;
            CuteUITheme.Apply(background, CuteUITheme.PanelCornerRadius);

            TMP_FontAsset font = m_InputField.textComponent != null
                ? m_InputField.textComponent.font
                : TMP_Settings.defaultFontAsset;

            for (int rowIndex = 0; rowIndex < s_Rows.Length; ++rowIndex)
                CreateRow(keyboardRect, font, s_Rows[rowIndex], rowIndex);

            return keyboard;
        }

        void CreateRow(RectTransform keyboardRect, TMP_FontAsset font, KeyDefinition[] row, int rowIndex)
        {
            float totalWeight = 0f;
            for (int i = 0; i < row.Length; ++i)
                totalWeight += row[i].Width;

            float usableWidth = keyboardRect.rect.width - k_Padding * 2f - k_KeyGap * (row.Length - 1);
            float unitWidth = usableWidth / totalWeight;
            float x = k_Padding;
            float y = k_Padding + rowIndex * (k_KeyHeight + k_RowGap);

            for (int i = 0; i < row.Length; ++i)
            {
                float width = unitWidth * row[i].Width;
                CreateKey(keyboardRect, font, row[i], x, y, width);
                x += width + k_KeyGap;
            }
        }

        void CreateKey(
            RectTransform keyboardRect,
            TMP_FontAsset font,
            KeyDefinition definition,
            float x,
            float y,
            float width)
        {
            GameObject key = new($"Key {definition.Label}");
            key.layer = gameObject.layer;
            RectTransform rect = key.AddComponent<RectTransform>();
            rect.SetParent(keyboardRect, false);
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = new Vector2(x, -y);
            rect.sizeDelta = new Vector2(width, k_KeyHeight);

            bool actionKey = definition.Value.Length > 0 && definition.Value[0] == '{';
            Image image = key.AddComponent<Image>();
            image.color = actionKey ? s_ActionKeyColor : s_KeyColor;
            CuteUITheme.Apply(image, CuteUITheme.ControlCornerRadius);
            Button button = key.AddComponent<Button>();
            button.targetGraphic = image;
            ColorBlock colors = button.colors;
            colors.normalColor = image.color;
            colors.highlightedColor = Color.Lerp(image.color, Color.white, 0.12f);
            colors.pressedColor = Color.Lerp(image.color, Color.black, 0.18f);
            colors.selectedColor = colors.highlightedColor;
            button.colors = colors;
            Navigation navigation = button.navigation;
            navigation.mode = Navigation.Mode.None;
            button.navigation = navigation;
            string value = definition.Value;
            button.onClick.AddListener(() => Press(value));

            GameObject labelObject = new("Label");
            labelObject.layer = gameObject.layer;
            RectTransform labelRect = labelObject.AddComponent<RectTransform>();
            labelRect.SetParent(rect, false);
            labelRect.anchorMin = Vector2.zero;
            labelRect.anchorMax = Vector2.one;
            labelRect.offsetMin = Vector2.zero;
            labelRect.offsetMax = Vector2.zero;

            TextMeshProUGUI label = labelObject.AddComponent<TextMeshProUGUI>();
            label.font = font;
            label.fontSize = 27f;
            label.color = Color.white;
            label.alignment = TextAlignmentOptions.Center;
            label.text = definition.Label;
            label.raycastTarget = false;

            if (definition.Value.Length == 1 && char.IsLetter(definition.Value[0]))
                m_LetterLabels.Add((label, definition.Value));
        }

        void Press(string value)
        {
            switch (value)
            {
                case k_Shift:
                    m_Shifted = !m_Shifted;
                    UpdateLetterLabels();
                    return;
                case k_Backspace:
                    Backspace();
                    break;
                case k_Space:
                    Insert(" ");
                    break;
                case k_Cancel:
                    m_InputField.text = m_OriginalText;
                    Close();
                    return;
                case k_Close:
                    Close();
                    return;
                case k_Submit:
                    Close();
                    m_InputField.OnSubmit(null);
                    return;
                default:
                    Insert(m_Shifted ? value.ToUpperInvariant() : value);
                    if (m_Shifted)
                    {
                        m_Shifted = false;
                        UpdateLetterLabels();
                    }
                    break;
            }

            m_InputField.ActivateInputField();
        }

        void Insert(string value)
        {
            int anchor = Mathf.Clamp(m_InputField.selectionStringAnchorPosition, 0, m_InputField.text.Length);
            int focus = Mathf.Clamp(m_InputField.selectionStringFocusPosition, 0, m_InputField.text.Length);
            int start = Mathf.Min(anchor, focus);
            int length = Mathf.Abs(anchor - focus);
            string text = m_InputField.text.Remove(start, length).Insert(start, value);
            m_InputField.text = text;
            m_InputField.stringPosition = start + value.Length;
        }

        void Backspace()
        {
            int anchor = Mathf.Clamp(m_InputField.selectionStringAnchorPosition, 0, m_InputField.text.Length);
            int focus = Mathf.Clamp(m_InputField.selectionStringFocusPosition, 0, m_InputField.text.Length);
            int start = Mathf.Min(anchor, focus);
            int length = Mathf.Abs(anchor - focus);
            if (length == 0 && start > 0)
            {
                --start;
                length = 1;
            }

            if (length == 0)
                return;

            m_InputField.text = m_InputField.text.Remove(start, length);
            m_InputField.stringPosition = start;
        }

        void UpdateLetterLabels()
        {
            for (int i = 0; i < m_LetterLabels.Count; ++i)
            {
                (TMP_Text label, string lowerCase) = m_LetterLabels[i];
                label.text = m_Shifted ? lowerCase.ToUpperInvariant() : lowerCase;
            }
        }

        public void Close()
        {
            if (m_Keyboard != null)
                Destroy(m_Keyboard);
            m_Keyboard = null;
            m_LetterLabels.Clear();
        }

        void OnDisable() => Close();
        void OnDestroy() => Close();
    }
}
