using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace Ellmo.XR
{
    /// <summary>
    /// Adds a small, asset-free manipulation shelf to a world-space canvas.
    /// The grab area moves the panel with either a tracked ray or fingertip;
    /// the adjacent buttons resize, reset, or recenter it.
    /// </summary>
    [DisallowMultipleComponent]
    [RequireComponent(typeof(Canvas))]
    public sealed class XRCanvasManipulator : MonoBehaviour
    {
        const string k_ControlsName = "XR Panel Controls";

        Transform m_Head;
        Transform m_TrackingSpace;
        Canvas m_Canvas;
        TMP_Text m_ScaleLabel;
        float m_DefaultDistance = 0.75f;
        float m_VerticalOffset = -0.12f;
        float m_DefaultScale = 0.00035f;
        float m_MinScale = 0.00018f;
        float m_MaxScale = 0.0007f;
        int m_ActivePointerId = int.MinValue;
        float m_GrabDistance;
        Vector3 m_GrabOffset;

        void Awake()
        {
            m_Canvas = GetComponent<Canvas>();
            BuildControls();
        }

        void OnDisable() => m_ActivePointerId = int.MinValue;

        public void Initialize(
            Transform head,
            Transform trackingSpace,
            float defaultDistance,
            float verticalOffset,
            float defaultScale,
            float minScale,
            float maxScale)
        {
            m_Head = head;
            m_TrackingSpace = trackingSpace;
            m_DefaultDistance = Mathf.Max(0.1f, defaultDistance);
            m_VerticalOffset = verticalOffset;
            m_MinScale = Mathf.Max(0.00001f, minScale);
            m_MaxScale = Mathf.Max(m_MinScale, maxScale);
            m_DefaultScale = Mathf.Clamp(defaultScale, m_MinScale, m_MaxScale);
            UpdateScaleLabel();
        }

        internal void BeginMove(PointerEventData eventData)
        {
            if (m_ActivePointerId != int.MinValue || !TryGetPointerRay(eventData, out Ray ray))
                return;

            m_ActivePointerId = eventData.pointerId;
            Vector3 hitPosition = eventData.pointerPressRaycast.isValid
                ? eventData.pointerPressRaycast.worldPosition
                : transform.position;
            m_GrabDistance = Mathf.Max(0.05f, Vector3.Dot(hitPosition - ray.origin, ray.direction));
            m_GrabOffset = transform.position - ray.GetPoint(m_GrabDistance);
        }

        internal void Move(PointerEventData eventData)
        {
            if (m_ActivePointerId != eventData.pointerId || !TryGetPointerRay(eventData, out Ray ray))
                return;

            transform.position = ray.GetPoint(m_GrabDistance) + m_GrabOffset;
            FaceHead();
        }

        internal void EndMove(PointerEventData eventData)
        {
            if (m_ActivePointerId != eventData.pointerId)
                return;

            m_ActivePointerId = int.MinValue;
            FaceHead();
        }

        void ChangeScale(float multiplier)
        {
            float scale = Mathf.Clamp(transform.localScale.x * multiplier, m_MinScale, m_MaxScale);
            transform.localScale = Vector3.one * scale;
            UpdateScaleLabel();
        }

        void ResetScale()
        {
            transform.localScale = Vector3.one * m_DefaultScale;
            UpdateScaleLabel();
        }

        void Recenter()
        {
            if (m_Head == null)
                return;

            Vector3 up = m_TrackingSpace != null ? m_TrackingSpace.up : Vector3.up;
            Vector3 forward = Vector3.ProjectOnPlane(m_Head.forward, up).normalized;
            if (forward.sqrMagnitude < 0.01f)
                forward = m_Head.forward.normalized;

            Vector3 viewAnchor = m_Head.position + up * m_VerticalOffset;
            transform.SetPositionAndRotation(
                viewAnchor + forward * m_DefaultDistance,
                Quaternion.LookRotation(forward, up));
        }

        void FaceHead()
        {
            if (m_Head == null)
                return;

            Vector3 up = m_TrackingSpace != null ? m_TrackingSpace.up : Vector3.up;
            Vector3 viewAnchor = m_Head.position + up * m_VerticalOffset;
            Vector3 forward = transform.position - viewAnchor;
            if (forward.sqrMagnitude < 0.01f)
                return;

            transform.rotation = Quaternion.LookRotation(forward.normalized, up);
        }

        bool TryGetPointerRay(PointerEventData eventData, out Ray ray)
        {
            if (eventData is ExtendedPointerEventData tracked && tracked.pointerType == UIPointerType.Tracked)
            {
                ray = new Ray(tracked.trackedDevicePosition, tracked.trackedDeviceOrientation * Vector3.forward);
                return true;
            }

            Camera eventCamera = m_Canvas != null ? m_Canvas.worldCamera : null;
            if (eventCamera != null)
            {
                ray = eventCamera.ScreenPointToRay(eventData.position);
                return true;
            }

            ray = default;
            return false;
        }

        void BuildControls()
        {
            Transform existing = transform.Find(k_ControlsName);
            if (existing != null)
            {
                m_ScaleLabel = existing.Find("Scale/Label")?.GetComponent<TMP_Text>();
                return;
            }

            GameObject shelf = CreateImageObject(k_ControlsName, transform, new Color32(24, 31, 43, 245));
            RectTransform shelfRect = (RectTransform)shelf.transform;
            shelfRect.anchorMin = new Vector2(0.5f, 0f);
            shelfRect.anchorMax = new Vector2(0.5f, 0f);
            shelfRect.pivot = new Vector2(0.5f, 1f);
            shelfRect.anchoredPosition = new Vector2(0f, -24f);
            shelfRect.sizeDelta = new Vector2(720f, 82f);

            HorizontalLayoutGroup layout = shelf.AddComponent<HorizontalLayoutGroup>();
            layout.padding = new RectOffset(10, 10, 10, 10);
            layout.spacing = 8f;
            layout.childAlignment = TextAnchor.MiddleCenter;
            layout.childControlWidth = false;
            layout.childControlHeight = false;
            layout.childForceExpandWidth = false;
            layout.childForceExpandHeight = false;

            GameObject move = CreateImageObject("Move", shelf.transform, new Color32(54, 66, 84, 255));
            SetLayout(move, 260f, 62f);
            ConfigureButton(move);
            XRCanvasMoveHandle moveHandle = move.AddComponent<XRCanvasMoveHandle>();
            moveHandle.Initialize(this);
            CreateLabel(move.transform, "Label", "Grab to move", 25f);

            Button smaller = CreateButton(shelf.transform, "Smaller", "-", 62f);
            smaller.onClick.AddListener(() => ChangeScale(1f / 1.2f));

            Button scale = CreateButton(shelf.transform, "Scale", "100%", 98f);
            m_ScaleLabel = scale.transform.Find("Label").GetComponent<TMP_Text>();
            scale.onClick.AddListener(ResetScale);

            Button larger = CreateButton(shelf.transform, "Larger", "+", 62f);
            larger.onClick.AddListener(() => ChangeScale(1.2f));

            Button center = CreateButton(shelf.transform, "Center", "Center", 176f);
            center.onClick.AddListener(Recenter);

            UpdateScaleLabel();
        }

        static GameObject CreateImageObject(string name, Transform parent, Color color)
        {
            GameObject result = new(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            result.layer = parent.gameObject.layer;
            result.transform.SetParent(parent, false);
            Image image = result.GetComponent<Image>();
            image.color = color;
            CuteUITheme.Apply(image, CuteUITheme.ControlCornerRadius);
            return result;
        }

        static Button CreateButton(Transform parent, string name, string label, float width)
        {
            GameObject buttonObject = CreateImageObject(name, parent, new Color32(54, 66, 84, 255));
            SetLayout(buttonObject, width, 62f);
            Button button = ConfigureButton(buttonObject);
            CreateLabel(buttonObject.transform, "Label", label, 24f);
            return button;
        }

        static Button ConfigureButton(GameObject buttonObject)
        {
            Button button = buttonObject.AddComponent<Button>();
            button.targetGraphic = buttonObject.GetComponent<Image>();
            ColorBlock colors = button.colors;
            colors.highlightedColor = new Color32(74, 91, 116, 255);
            colors.pressedColor = new Color32(35, 173, 230, 255);
            colors.selectedColor = colors.highlightedColor;
            button.colors = colors;
            return button;
        }

        static void CreateLabel(Transform parent, string name, string text, float fontSize)
        {
            GameObject labelObject = new(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            labelObject.layer = parent.gameObject.layer;
            labelObject.transform.SetParent(parent, false);
            RectTransform rect = (RectTransform)labelObject.transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            TMP_Text label = labelObject.GetComponent<TMP_Text>();
            label.text = text;
            label.fontSize = fontSize;
            label.alignment = TextAlignmentOptions.Center;
            label.color = Color.white;
            label.raycastTarget = false;
        }

        static void SetLayout(GameObject target, float width, float height)
        {
            LayoutElement element = target.AddComponent<LayoutElement>();
            element.preferredWidth = width;
            element.preferredHeight = height;
            element.minWidth = width;
            element.minHeight = height;
        }

        void UpdateScaleLabel()
        {
            if (m_ScaleLabel == null || m_DefaultScale <= 0f)
                return;

            m_ScaleLabel.text = $"{Mathf.RoundToInt(transform.localScale.x / m_DefaultScale * 100f)}%";
        }
    }

    sealed class XRCanvasMoveHandle : MonoBehaviour,
        IInitializePotentialDragHandler,
        IBeginDragHandler,
        IDragHandler,
        IEndDragHandler
    {
        XRCanvasManipulator m_Owner;

        public void Initialize(XRCanvasManipulator owner) => m_Owner = owner;

        public void OnInitializePotentialDrag(PointerEventData eventData) => eventData.useDragThreshold = false;

        public void OnBeginDrag(PointerEventData eventData) => m_Owner?.BeginMove(eventData);

        public void OnDrag(PointerEventData eventData) => m_Owner?.Move(eventData);

        public void OnEndDrag(PointerEventData eventData) => m_Owner?.EndMove(eventData);
    }
}
