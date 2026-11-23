using System.Collections;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.InputSystem.UI;
using UnityEngine.InputSystem.XR;
using UnityEngine.XR;
using UnityEngine.XR.Hands;
using UnityEngine.XR.Hands.Configuration;

namespace Ellmo.XR
{
    [DefaultExecutionOrder(-1000)]
    public sealed class OpenXRHandUI : MonoBehaviour
    {
        static readonly List<XRHandSubsystem> s_HandSubsystems = new();
        const string k_OpenXRBindings = "OpenXRHands";
        const string k_MetaFallbackBindings = "MetaFallback";
        const string k_NearTouchBindings = "NearTouch";
        const float k_HeadPoseTimeout = 5f;
        static readonly Vector3 k_DisabledTouchPosition = new(10000f, 10000f, 10000f);

        [Header("OpenXR Rig")]
        [SerializeField] InputSystemUIInputModule inputModule;
        [SerializeField] Transform trackingSpace;
        [SerializeField] Transform head;

        [Header("One-time Workspace Placement")]
        [SerializeField] Transform panel;
        [SerializeField, Min(0.1f)] float panelDistance = 0.75f;
        [SerializeField] float panelVerticalOffset = -0.12f;
        [SerializeField, Min(0.00001f)] float panelScale = 0.00035f;
        [SerializeField] Transform pdfViewer;
        [SerializeField, Min(0.1f)] float pdfDistance = 0.9f;
        [SerializeField] float pdfHorizontalOffset = 0.62f;
        [SerializeField] float pdfVerticalOffset = -0.08f;
        [SerializeField] Transform cameraFeed;
        [SerializeField, Min(0.1f)] float cameraFeedDistance = 0.82f;
        [SerializeField] float cameraFeedHorizontalOffset = -0.58f;
        [SerializeField] float cameraFeedVerticalOffset = -0.04f;
        [SerializeField, Min(0.5f)] float fallbackEyeHeight = 1.6f;

        [Header("Panel Manipulation")]
        [SerializeField, Min(0.00001f)] float minimumPanelScale = 0.00018f;
        [SerializeField, Min(0.00001f)] float maximumPanelScale = 0.0007f;

        [Header("Fingertip UI Touch")]
        [SerializeField, Min(0.005f)] float touchHoverDistance = 0.04f;
        [SerializeField, Min(0f)] float touchPressDistance = 0.004f;
        [SerializeField, Min(0f)] float touchReleaseDistance = 0.012f;
        [SerializeField, Min(0.01f)] float touchCastBackDistance = 0.06f;

        [Header("Hand Feedback")]
        [SerializeField] Material visualMaterial;
        [SerializeField, Min(0.001f)] float jointSize = 0.012f;
        [SerializeField, Min(0.1f)] float rayLength = 3f;
        [SerializeField, Range(0.1f, 1f)] float pinchThreshold = 0.75f;
        [SerializeField] Color handColor = new(0.05f, 0.85f, 1f, 1f);
        [SerializeField] Color pinchColor = new(1f, 0.75f, 0.05f, 1f);

        [Header("Voice Gesture")]
        [Tooltip("Face an open palm toward the headset, close it into a fist, and hold briefly to toggle voice recording.")]
        [SerializeField] bool enableVoiceGesture = false;
        [Tooltip("Minimum dot product between the palm direction and the direction to the headset.")]
        [SerializeField, Range(-1f, 1f)] float palmFacingHeadThreshold = 0.35f;
        [SerializeField, Min(0.05f)] float fistHoldDuration = 0.15f;
        [SerializeField, Min(0f)] float voiceGestureCooldown = 0.25f;

        InputAction m_ClickAction;
        InputAction m_PositionAction;
        InputAction m_RotationAction;
        InputActionAsset m_RuntimeActions;
        InputActionReference m_ClickReference;
        InputActionReference m_PositionReference;
        InputActionReference m_RotationReference;
        XRHandSubsystem m_HandSubsystem;
        XRHandSubsystem m_ConfiguredHandSubsystem;
        HandVisual m_LeftVisual;
        HandVisual m_RightVisual;
        Material m_HandMaterial;
        Material m_PinchMaterial;
        OpenXRNearTouchDevice m_LeftTouchDevice;
        OpenXRNearTouchDevice m_RightTouchDevice;
        readonly List<RaycastResult> m_TouchRaycastResults = new();
        ExtendedPointerEventData m_TouchRaycastEvent;
        EventSystem m_EventSystem;
        Canvas m_UICanvas;
        TMP_Dropdown[] m_Dropdowns;
        bool m_LeftTouchPressed;
        bool m_RightTouchPressed;
        float m_SubsystemWaitStart;
        bool m_MissingSubsystemReported;
        string m_ActiveBindingGroup;
        VoiceManager m_VoiceManager;
        FistGestureState m_LeftVoiceFist;
        FistGestureState m_RightVoiceFist;
        float m_VoiceGestureCooldownUntil;
        VoiceGestureCue m_PendingVoiceGestureCue;
        AudioSource m_VoiceGestureAudioSource;
        AudioClip m_StartRecordingClip;
        AudioClip m_StopRecordingClip;
        bool m_MissingVoiceManagerReported;

        struct FistGestureState
        {
            public bool wasFist;
            public bool armed;
            public bool triggered;
            public float fistStartedAt;
        }

        enum VoiceGestureCue
        {
            None,
            Start,
            Stop,
        }

        Transform TrackingSpace => trackingSpace != null
            ? trackingSpace
            : head != null && head.parent != null
                ? head.parent
                : transform;

        void Awake()
        {
            if (inputModule == null)
                inputModule = GetComponent<InputSystemUIInputModule>();

            ConfigureDropdownCanvases();
            ConfigureXRControls();
            CreateNearTouchDevices();
            ConfigureUIActions();
            CreateMaterials();
            ResolveVoiceManager();
            m_SubsystemWaitStart = Time.unscaledTime;
        }

        IEnumerator Start()
        {
            // Do not capture the scene's zero camera pose. OpenXR and the tracked-pose
            // driver can take several frames to publish the first valid HMD pose.
            float deadline = Time.unscaledTime + k_HeadPoseTimeout;
            while (!HasTrackedHeadPose() && Time.unscaledTime < deadline)
                yield return null;

            // Let the tracked-pose driver and XR Origin consume that input update.
            yield return new WaitForEndOfFrame();
            PlaceWorkspaceOnce();
        }

        void Update()
        {
            FindAndConfigureHandSubsystem();

            if (m_HandSubsystem == null || !m_HandSubsystem.running)
            {
                SetHandsVisible(false);
                DisableNearTouchDevices();
                ResetVoiceGestureRecognition();
                if (!m_MissingSubsystemReported && Time.unscaledTime - m_SubsystemWaitStart > 3f)
                {
                    m_MissingSubsystemReported = true;
                    Debug.LogWarning("[Ellmo OpenXR] XR Hands did not start. Check that the OpenXR loader and Hand Tracking Subsystem feature are enabled for this build target.");
                }
                return;
            }

            EnsureHandVisuals();
            SelectUIInputSource();
            m_LeftTouchPressed = UpdateNearTouch(m_HandSubsystem.leftHand, m_LeftTouchDevice, m_LeftTouchPressed);
            m_RightTouchPressed = UpdateNearTouch(m_HandSubsystem.rightHand, m_RightTouchDevice, m_RightTouchPressed);
            UpdateHand(m_HandSubsystem.leftHand, XRHandDevice.leftHand, MetaAimHand.left, m_LeftVisual, m_LeftTouchPressed);
            UpdateHand(m_HandSubsystem.rightHand, XRHandDevice.rightHand, MetaAimHand.right, m_RightVisual, m_RightTouchPressed);
            UpdateVoiceGesture(
                m_HandSubsystem.leftHand,
                XRHandDevice.leftHand,
                ref m_LeftVoiceFist);
            UpdateVoiceGesture(
                m_HandSubsystem.rightHand,
                XRHandDevice.rightHand,
                ref m_RightVoiceFist);
        }

        void LateUpdate()
        {
            if (m_UICanvas == null || m_Dropdowns == null)
                return;

            for (int i = 0; i < m_Dropdowns.Length; ++i)
            {
                if (m_Dropdowns[i] != null && m_Dropdowns[i].IsExpanded)
                {
                    RefreshPopupCanvasCameras();
                    return;
                }
            }
        }

        void OnDisable()
        {
            SetHandsVisible(false);
            DisableNearTouchDevices();
            ResetVoiceGestureRecognition();
        }

        void OnDestroy()
        {
            if (m_VoiceManager != null)
                m_VoiceManager.StateChanged -= OnVoiceStateChanged;

            m_RuntimeActions?.Disable();

            if (m_ClickReference != null)
                Destroy(m_ClickReference);
            if (m_PositionReference != null)
                Destroy(m_PositionReference);
            if (m_RotationReference != null)
                Destroy(m_RotationReference);
            if (m_RuntimeActions != null)
                Destroy(m_RuntimeActions);

            if (m_LeftTouchDevice != null && m_LeftTouchDevice.added)
                InputSystem.RemoveDevice(m_LeftTouchDevice);
            if (m_RightTouchDevice != null && m_RightTouchDevice.added)
                InputSystem.RemoveDevice(m_RightTouchDevice);

            m_LeftVisual?.Destroy();
            m_RightVisual?.Destroy();
            if (m_HandMaterial != null)
                Destroy(m_HandMaterial);
            if (m_PinchMaterial != null)
                Destroy(m_PinchMaterial);
            if (m_StartRecordingClip != null)
                Destroy(m_StartRecordingClip);
            if (m_StopRecordingClip != null)
                Destroy(m_StopRecordingClip);
        }

        void PlaceWorkspaceOnce()
        {
            if (head == null)
                return;

            Transform origin = TrackingSpace.parent != null ? TrackingSpace.parent : TrackingSpace;
            Vector3 up = origin.up;
            Vector3 eyePosition = head.position;
            float trackedHeight = origin.InverseTransformPoint(eyePosition).y;
            if (trackedHeight < 0.5f)
            {
                eyePosition += up * (fallbackEyeHeight - trackedHeight);
                Debug.LogWarning("[Ellmo OpenXR] The initial head pose had no usable height; placing the workspace at the configured fallback eye height.");
            }

            Vector3 forward = Vector3.ProjectOnPlane(head.forward, up).normalized;
            if (forward.sqrMagnitude < 0.01f)
                forward = head.forward.normalized;

            if (panel != null)
            {
                panel.SetParent(TrackingSpace, true);
                panel.SetPositionAndRotation(
                    eyePosition + forward * panelDistance + up * panelVerticalOffset,
                    Quaternion.LookRotation(forward, up));
                panel.localScale = Vector3.one * panelScale;
            }

            if (pdfViewer != null)
            {
                Vector3 right = Vector3.Cross(up, forward).normalized;
                Vector3 pdfPosition = eyePosition +
                    forward * pdfDistance +
                    right * pdfHorizontalOffset +
                    up * pdfVerticalOffset;
                Vector3 pdfForward = (pdfPosition - eyePosition).normalized;

                pdfViewer.SetParent(TrackingSpace, true);
                pdfViewer.SetPositionAndRotation(pdfPosition, Quaternion.LookRotation(pdfForward, up));
            }

            if (cameraFeed != null)
            {
                Vector3 right = Vector3.Cross(up, forward).normalized;
                Vector3 feedPosition = eyePosition +
                    forward * cameraFeedDistance +
                    right * cameraFeedHorizontalOffset +
                    up * cameraFeedVerticalOffset;
                Vector3 feedForward = (feedPosition - eyePosition).normalized;

                cameraFeed.SetParent(TrackingSpace, true);
                cameraFeed.SetPositionAndRotation(feedPosition, Quaternion.LookRotation(feedForward, up));
            }

            Debug.Log($"[Ellmo OpenXR] Workspace placed from initial head pose at {eyePosition:F2}.");
        }

        static bool HasTrackedHeadPose()
        {
            XRHMD hmd = InputSystem.GetDevice<XRHMD>();
            if (hmd == null || !hmd.isTracked.isPressed)
                return false;

            InputTrackingState state = (InputTrackingState)hmd.trackingState.ReadValue();
            const InputTrackingState required = InputTrackingState.Position | InputTrackingState.Rotation;
            return (state & required) == required;
        }

        void ConfigureDropdownCanvases()
        {
            if (panel == null)
                return;

            m_UICanvas = panel.GetComponent<Canvas>();
            if (m_UICanvas == null || m_UICanvas.worldCamera == null)
                return;

            m_Dropdowns = panel.GetComponentsInChildren<TMP_Dropdown>(true);
            for (int i = 0; i < m_Dropdowns.Length; ++i)
            {
                RectTransform template = m_Dropdowns[i].template;
                if (template == null)
                    continue;

                Canvas popupCanvas = template.GetComponent<Canvas>();
                if (popupCanvas == null)
                    popupCanvas = template.gameObject.AddComponent<Canvas>();
                popupCanvas.worldCamera = m_UICanvas.worldCamera;
            }
        }

        void RefreshPopupCanvasCameras()
        {
            // TMP_Dropdown creates its option list and blocker as nested canvases.
            // TrackedDeviceRaycaster ignores those canvases unless they have a world camera.
            Canvas[] canvases = m_UICanvas.GetComponentsInChildren<Canvas>(true);
            for (int i = 0; i < canvases.Length; ++i)
            {
                if (canvases[i] != m_UICanvas && canvases[i].worldCamera == null)
                    canvases[i].worldCamera = m_UICanvas.worldCamera;
            }
        }

        void ConfigureXRControls()
        {
            if (panel == null)
                return;

            TMP_Dropdown[] dropdowns = panel.GetComponentsInChildren<TMP_Dropdown>(true);
            for (int i = 0; i < dropdowns.Length; ++i)
            {
                if (dropdowns[i].GetComponent<XRDropdownPopup>() == null)
                    dropdowns[i].gameObject.AddComponent<XRDropdownPopup>();
            }

            TMP_InputField[] inputFields = panel.GetComponentsInChildren<TMP_InputField>(true);
            for (int i = 0; i < inputFields.Length; ++i)
            {
                XRCanvasKeyboard keyboard = inputFields[i].GetComponent<XRCanvasKeyboard>();
                if (keyboard == null)
                    keyboard = inputFields[i].gameObject.AddComponent<XRCanvasKeyboard>();
                keyboard.RefreshBinding();
            }

            XRCanvasManipulator manipulator = panel.GetComponent<XRCanvasManipulator>();
            if (manipulator == null)
                manipulator = panel.gameObject.AddComponent<XRCanvasManipulator>();
            manipulator.Initialize(
                head,
                TrackingSpace,
                panelDistance,
                panelVerticalOffset,
                panelScale,
                minimumPanelScale,
                maximumPanelScale);
        }

        /// <summary>
        /// Wires controls created after Awake, such as the runtime server settings fields.
        /// </summary>
        public void RefreshRuntimeControls()
        {
            ConfigureDropdownCanvases();
            ConfigureXRControls();
        }

        void CreateNearTouchDevices()
        {
            InputSystem.RegisterLayout<OpenXRNearTouchDevice>();
            m_LeftTouchDevice = InputSystem.AddDevice<OpenXRNearTouchDevice>("Left OpenXR Near Touch");
            m_RightTouchDevice = InputSystem.AddDevice<OpenXRNearTouchDevice>("Right OpenXR Near Touch");
            InputSystem.SetDeviceUsage(m_LeftTouchDevice, UnityEngine.InputSystem.CommonUsages.LeftHand);
            InputSystem.SetDeviceUsage(m_RightTouchDevice, UnityEngine.InputSystem.CommonUsages.RightHand);

            m_EventSystem = inputModule != null ? inputModule.GetComponent<EventSystem>() : EventSystem.current;
            if (m_EventSystem != null)
            {
                m_TouchRaycastEvent = new ExtendedPointerEventData(m_EventSystem)
                {
                    pointerType = UIPointerType.Tracked,
                    button = PointerEventData.InputButton.Left,
                };
            }
        }

        void ConfigureUIActions()
        {
            if (inputModule == null)
                return;

            // UI pointer actions must be PassThrough so each hand/device keeps an
            // independent pointer instead of Input System disambiguating them.
            m_RuntimeActions = ScriptableObject.CreateInstance<InputActionAsset>();
            m_RuntimeActions.name = "Runtime OpenXR Hand UI Actions";
            InputActionMap actionMap = m_RuntimeActions.AddActionMap("OpenXR Hand UI");

            m_ClickAction = actionMap.AddAction(
                "OpenXR Hand Click",
                InputActionType.PassThrough,
                expectedControlLayout: "Button");
            m_ClickAction.AddBinding("<XRHandDevice>{LeftHand}/aimActivated").WithGroup(k_OpenXRBindings);
            m_ClickAction.AddBinding("<XRHandDevice>{RightHand}/aimActivated").WithGroup(k_OpenXRBindings);
            // Quest runtimes which do not expose XR_EXT_hand_interaction need the
            // vendor aim extension for the UI click. It remains only a fallback binding.
            m_ClickAction.AddBinding("<MetaAimHand>{LeftHand}/indexPressed").WithGroup(k_MetaFallbackBindings);
            m_ClickAction.AddBinding("<MetaAimHand>{RightHand}/indexPressed").WithGroup(k_MetaFallbackBindings);
            m_ClickAction.AddBinding("<OpenXRNearTouchDevice>{LeftHand}/touch").WithGroup(k_NearTouchBindings);
            m_ClickAction.AddBinding("<OpenXRNearTouchDevice>{RightHand}/touch").WithGroup(k_NearTouchBindings);

            m_PositionAction = actionMap.AddAction(
                "OpenXR Hand Aim Position",
                InputActionType.PassThrough,
                expectedControlLayout: "Vector3");
            m_PositionAction.AddBinding("<XRHandDevice>{LeftHand}/aimPosition").WithGroup(k_OpenXRBindings);
            m_PositionAction.AddBinding("<XRHandDevice>{RightHand}/aimPosition").WithGroup(k_OpenXRBindings);
            m_PositionAction.AddBinding("<MetaAimHand>{LeftHand}/devicePosition").WithGroup(k_MetaFallbackBindings);
            m_PositionAction.AddBinding("<MetaAimHand>{RightHand}/devicePosition").WithGroup(k_MetaFallbackBindings);
            m_PositionAction.AddBinding("<OpenXRNearTouchDevice>{LeftHand}/devicePosition").WithGroup(k_NearTouchBindings);
            m_PositionAction.AddBinding("<OpenXRNearTouchDevice>{RightHand}/devicePosition").WithGroup(k_NearTouchBindings);

            m_RotationAction = actionMap.AddAction(
                "OpenXR Hand Aim Rotation",
                InputActionType.PassThrough,
                expectedControlLayout: "Quaternion");
            m_RotationAction.AddBinding("<XRHandDevice>{LeftHand}/aimRotation").WithGroup(k_OpenXRBindings);
            m_RotationAction.AddBinding("<XRHandDevice>{RightHand}/aimRotation").WithGroup(k_OpenXRBindings);
            m_RotationAction.AddBinding("<MetaAimHand>{LeftHand}/deviceRotation").WithGroup(k_MetaFallbackBindings);
            m_RotationAction.AddBinding("<MetaAimHand>{RightHand}/deviceRotation").WithGroup(k_MetaFallbackBindings);
            m_RotationAction.AddBinding("<OpenXRNearTouchDevice>{LeftHand}/deviceRotation").WithGroup(k_NearTouchBindings);
            m_RotationAction.AddBinding("<OpenXRNearTouchDevice>{RightHand}/deviceRotation").WithGroup(k_NearTouchBindings);

            SetBindingGroup(k_OpenXRBindings);

            m_ClickReference = InputActionReference.Create(m_ClickAction);
            m_PositionReference = InputActionReference.Create(m_PositionAction);
            m_RotationReference = InputActionReference.Create(m_RotationAction);

            inputModule.leftClick = m_ClickReference;
            inputModule.trackedDevicePosition = m_PositionReference;
            inputModule.trackedDeviceOrientation = m_RotationReference;
            inputModule.xrTrackingOrigin = TrackingSpace;
            inputModule.pointerBehavior = UIPointerBehavior.AllPointersAsIs;
        }

        void SelectUIInputSource()
        {
            bool hasStandardAim =
                XRHandDevice.leftHand != null && XRHandDevice.leftHand.aimTrackingState.ReadValue() != 0 ||
                XRHandDevice.rightHand != null && XRHandDevice.rightHand.aimTrackingState.ReadValue() != 0;
            bool hasMetaAim =
                MetaAimHand.left != null && MetaAimHand.left.trackingState.ReadValue() != 0 ||
                MetaAimHand.right != null && MetaAimHand.right.trackingState.ReadValue() != 0;

            string desiredGroup = hasStandardAim || !hasMetaAim ? k_OpenXRBindings : k_MetaFallbackBindings;
            if (desiredGroup == m_ActiveBindingGroup)
                return;

            SetBindingGroup(desiredGroup);
            if (desiredGroup == k_MetaFallbackBindings)
                Debug.Log("[Ellmo OpenXR] The standard hand interaction profile is unavailable; using the Meta OpenXR aim extension as the UI fallback.");
        }

        void SetBindingGroup(string group)
        {
            InputBinding mask = InputBinding.MaskByGroups(group, k_NearTouchBindings);
            m_ClickAction.bindingMask = mask;
            m_PositionAction.bindingMask = mask;
            m_RotationAction.bindingMask = mask;
            m_ActiveBindingGroup = group;
        }

        void FindAndConfigureHandSubsystem()
        {
            if (m_HandSubsystem != null && m_HandSubsystem.running)
                return;

            s_HandSubsystems.Clear();
            SubsystemManager.GetSubsystems(s_HandSubsystems);
            m_HandSubsystem = null;
            for (int i = 0; i < s_HandSubsystems.Count; ++i)
            {
                if (s_HandSubsystems[i].running)
                {
                    m_HandSubsystem = s_HandSubsystems[i];
                    break;
                }
            }

            if (m_HandSubsystem == null || m_ConfiguredHandSubsystem == m_HandSubsystem)
                return;

            m_HandSubsystem.UpdateHandsConfiguration(new XRHandSubsystemConfiguration
            {
                xrHandDevicePoseSource = XRHandDevicePoseSource.CommonGestures,
            });
            m_ConfiguredHandSubsystem = m_HandSubsystem;
            m_MissingSubsystemReported = false;
            Debug.Log("[Ellmo OpenXR] XR Hands is running; fingertip touch and pinch-ray UI input are active.");
        }

        bool UpdateNearTouch(XRHand hand, OpenXRNearTouchDevice touchDevice, bool wasPressed)
        {
            if (touchDevice == null || !hand.isTracked ||
                !hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose tip) ||
                !hand.GetJoint(XRHandJointID.IndexDistal).TryGetPose(out Pose distal))
            {
                SetNearTouchState(touchDevice, k_DisabledTouchPosition, Quaternion.identity, false, false);
                return false;
            }

            Vector3 direction = (tip.position - distal.position).normalized;
            if (direction.sqrMagnitude < 0.5f)
            {
                SetNearTouchState(touchDevice, k_DisabledTouchPosition, Quaternion.identity, false, false);
                return false;
            }

            Vector3 localOrigin = tip.position - direction * touchCastBackDistance;
            Vector3 localUp = tip.rotation * Vector3.up;
            if (Mathf.Abs(Vector3.Dot(direction, localUp)) > 0.98f)
                localUp = tip.rotation * Vector3.right;
            Quaternion localRotation = Quaternion.LookRotation(direction, localUp);

            float surfaceDistance = FindNearTouchSurfaceDistance(localOrigin, localRotation);
            bool hovering = surfaceDistance <= touchHoverDistance;
            bool pressed = hovering && surfaceDistance <= (wasPressed ? touchReleaseDistance : touchPressDistance);

            SetNearTouchState(
                touchDevice,
                hovering ? localOrigin : k_DisabledTouchPosition,
                hovering ? localRotation : Quaternion.identity,
                hovering,
                pressed);
            return pressed;
        }

        float FindNearTouchSurfaceDistance(Vector3 localOrigin, Quaternion localRotation)
        {
            if (m_TouchRaycastEvent == null)
                return float.PositiveInfinity;

            m_TouchRaycastEvent.trackedDevicePosition = TrackingSpace.TransformPoint(localOrigin);
            m_TouchRaycastEvent.trackedDeviceOrientation = TrackingSpace.rotation * localRotation;
            m_TouchRaycastResults.Clear();
            m_EventSystem.RaycastAll(m_TouchRaycastEvent, m_TouchRaycastResults);

            float nearest = float.PositiveInfinity;
            for (int i = 0; i < m_TouchRaycastResults.Count; ++i)
            {
                RaycastResult result = m_TouchRaycastResults[i];
                if (result.module is TrackedDeviceRaycaster && result.distance < nearest)
                    nearest = result.distance;
            }

            return nearest - touchCastBackDistance;
        }

        static void SetNearTouchState(
            OpenXRNearTouchDevice device,
            Vector3 position,
            Quaternion rotation,
            bool tracked,
            bool pressed)
        {
            if (device == null || !device.added)
                return;

            var state = new OpenXRNearTouchState
            {
                trackingState = tracked ? (int)(InputTrackingState.Position | InputTrackingState.Rotation) : 0,
                isTracked = tracked ? (byte)1 : (byte)0,
                touch = pressed ? (byte)1 : (byte)0,
                position = position,
                rotation = rotation,
            };
            InputState.Change(device, state);
        }

        void DisableNearTouchDevices()
        {
            m_LeftTouchPressed = false;
            m_RightTouchPressed = false;
            SetNearTouchState(m_LeftTouchDevice, k_DisabledTouchPosition, Quaternion.identity, false, false);
            SetNearTouchState(m_RightTouchDevice, k_DisabledTouchPosition, Quaternion.identity, false, false);
        }

        void CreateMaterials()
        {
            Shader shader = visualMaterial != null ? visualMaterial.shader : Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                shader = Shader.Find("Unlit/Color");
            if (shader == null)
                return;

            m_HandMaterial = visualMaterial != null ? new Material(visualMaterial) : new Material(shader);
            m_PinchMaterial = visualMaterial != null ? new Material(visualMaterial) : new Material(shader);
            m_HandMaterial.name = "Runtime OpenXR Hand";
            m_PinchMaterial.name = "Runtime OpenXR Pinch";
            SetMaterialColor(m_HandMaterial, handColor);
            SetMaterialColor(m_PinchMaterial, pinchColor);
        }

        static void SetMaterialColor(Material material, Color color)
        {
            if (material.HasProperty("_BaseColor"))
                material.SetColor("_BaseColor", color);
            if (material.HasProperty("_Color"))
                material.SetColor("_Color", color);
        }

        void EnsureHandVisuals()
        {
            if (m_HandMaterial == null)
                return;

            m_LeftVisual ??= new HandVisual("Left Hand", TrackingSpace, jointSize, m_HandMaterial, m_PinchMaterial);
            m_RightVisual ??= new HandVisual("Right Hand", TrackingSpace, jointSize, m_HandMaterial, m_PinchMaterial);
        }

        void UpdateHand(XRHand hand, XRHandDevice device, MetaAimHand metaAim, HandVisual visual, bool touching)
        {
            if (visual == null)
                return;

            bool tracked = hand.isTracked;
            visual.SetVisible(tracked);
            if (!tracked)
                return;

            float pinchAmount = GetPinchAmount(hand, device, metaAim);
            visual.UpdateJoints(hand, pinchAmount, pinchThreshold, touching);

            if (TryGetAimPose(hand, device, metaAim, out Pose aimPose))
                visual.UpdateRay(aimPose, rayLength, pinchAmount, pinchThreshold, handColor, pinchColor);
            else
                visual.HideRay();
        }

        void UpdateVoiceGesture(
            XRHand hand,
            XRHandDevice device,
            ref FistGestureState state)
        {
            if (!enableVoiceGesture || !hand.isTracked ||
                !TryGetVoiceFistState(hand, device, state.wasFist, out bool isFist))
            {
                state = default;
                return;
            }

            float now = Time.unscaledTime;
            bool palmFacingHead = IsPalmFacingHead(hand);

            if (!isFist)
            {
                // An open palm facing the user arms the next closure. Reopening
                // is required after every toggle, so holding a fist fires once.
                state.wasFist = false;
                state.armed = palmFacingHead;
                state.triggered = false;
                state.fistStartedAt = 0f;
                return;
            }

            if (!state.wasFist)
            {
                state.wasFist = true;
                state.fistStartedAt = now;

                if (!palmFacingHead)
                    state.armed = false;
            }

            if (!state.armed || state.triggered || !palmFacingHead ||
                now - state.fistStartedAt < fistHoldDuration ||
                now < m_VoiceGestureCooldownUntil)
                return;

            state.triggered = true;
            state.armed = false;
            m_VoiceGestureCooldownUntil = now + voiceGestureCooldown;

            // Disarm both hands so two fists detected in the same frame cannot
            // immediately start and stop recording again.
            m_LeftVoiceFist.armed = false;
            m_LeftVoiceFist.triggered = true;
            m_RightVoiceFist.armed = false;
            m_RightVoiceFist.triggered = true;
            ToggleVoiceRecordingFromGesture();
        }

        bool IsPalmFacingHead(XRHand hand)
        {
            if (head == null)
                return false;

            Pose rootPose = hand.rootPose;
            Vector3 palmDirection = rootPose.rotation * Vector3.down;
            Vector3 localHeadPosition = TrackingSpace.InverseTransformPoint(head.position);
            Vector3 directionToHead = localHeadPosition - rootPose.position;
            if (directionToHead.sqrMagnitude < 0.0001f)
                return false;

            return Vector3.Dot(palmDirection, directionToHead.normalized) >= palmFacingHeadThreshold;
        }

        bool TryGetVoiceFistState(
            XRHand hand,
            XRHandDevice device,
            bool wasFist,
            out bool isFist)
        {
            // Prefer the runtime's filtered OpenXR firm-grasp classification.
            if (device != null && device.graspReady.isPressed)
            {
                isFist = device.graspFirm.isPressed;
                return true;
            }

            // Joint-only fallback for runtimes without the hand interaction
            // profile. Scale the threshold to the user's palm width and retain
            // a little hysteresis while the hand is already closed.
            if (hand.GetJoint(XRHandJointID.Palm).TryGetPose(out Pose palm) &&
                hand.GetJoint(XRHandJointID.IndexMetacarpal).TryGetPose(out Pose indexMetacarpal) &&
                hand.GetJoint(XRHandJointID.LittleMetacarpal).TryGetPose(out Pose littleMetacarpal) &&
                hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose indexTip) &&
                hand.GetJoint(XRHandJointID.MiddleTip).TryGetPose(out Pose middleTip) &&
                hand.GetJoint(XRHandJointID.RingTip).TryGetPose(out Pose ringTip) &&
                hand.GetJoint(XRHandJointID.LittleTip).TryGetPose(out Pose littleTip))
            {
                float palmWidth = Vector3.Distance(indexMetacarpal.position, littleMetacarpal.position);
                if (palmWidth < 0.02f)
                {
                    isFist = false;
                    return false;
                }

                float largestTipDistance = Mathf.Max(
                    Vector3.Distance(indexTip.position, palm.position),
                    Vector3.Distance(middleTip.position, palm.position),
                    Vector3.Distance(ringTip.position, palm.position),
                    Vector3.Distance(littleTip.position, palm.position));
                float closedRatio = wasFist ? 1.35f : 1.15f;
                isFist = largestTipDistance / palmWidth <= closedRatio;
                return true;
            }

            isFist = false;
            return false;
        }

        void ToggleVoiceRecordingFromGesture()
        {
            ResolveVoiceManager();
            if (m_VoiceManager == null)
            {
                if (!m_MissingVoiceManagerReported)
                {
                    m_MissingVoiceManagerReported = true;
                    Debug.LogWarning("[Ellmo OpenXR] Voice gesture ignored because VoiceManager is unavailable.", this);
                }
                return;
            }

            switch (m_VoiceManager.State)
            {
                case VoiceManager.InteractionState.Idle:
                case VoiceManager.InteractionState.Error:
                    m_PendingVoiceGestureCue = VoiceGestureCue.Start;
                    m_VoiceManager.ToggleRecording();
                    break;
                case VoiceManager.InteractionState.Recording:
                    m_PendingVoiceGestureCue = VoiceGestureCue.Stop;
                    m_VoiceManager.ToggleRecording();
                    break;
            }
        }

        void ResolveVoiceManager()
        {
            if (m_VoiceManager != null)
                return;

            m_VoiceManager = FindFirstObjectByType<VoiceManager>();
            if (m_VoiceManager != null)
            {
                m_VoiceManager.StateChanged += OnVoiceStateChanged;
                m_MissingVoiceManagerReported = false;
            }
        }

        void OnVoiceStateChanged(VoiceManager.InteractionState state, string _)
        {
            if (m_PendingVoiceGestureCue == VoiceGestureCue.Start)
            {
                if (state == VoiceManager.InteractionState.Recording)
                {
                    PlayVoiceGestureCue(starting: true);
                    m_PendingVoiceGestureCue = VoiceGestureCue.None;
                }
                else if (state == VoiceManager.InteractionState.Error)
                {
                    m_PendingVoiceGestureCue = VoiceGestureCue.None;
                }
            }
            else if (m_PendingVoiceGestureCue == VoiceGestureCue.Stop &&
                     state == VoiceManager.InteractionState.Transcribing)
            {
                PlayVoiceGestureCue(starting: false);
                m_PendingVoiceGestureCue = VoiceGestureCue.None;
            }
        }

        void PlayVoiceGestureCue(bool starting)
        {
            if (m_VoiceGestureAudioSource == null)
            {
                m_VoiceGestureAudioSource = gameObject.AddComponent<AudioSource>();
                m_VoiceGestureAudioSource.playOnAwake = false;
                m_VoiceGestureAudioSource.loop = false;
                m_VoiceGestureAudioSource.spatialBlend = 0f;
                m_VoiceGestureAudioSource.volume = 0.55f;
            }

            AudioClip clip;
            if (starting)
            {
                m_StartRecordingClip ??= CreateVoiceGestureClip(starting: true);
                clip = m_StartRecordingClip;
            }
            else
            {
                m_StopRecordingClip ??= CreateVoiceGestureClip(starting: false);
                clip = m_StopRecordingClip;
            }

            m_VoiceGestureAudioSource.PlayOneShot(clip);
        }

        static AudioClip CreateVoiceGestureClip(bool starting)
        {
            const int sampleRate = 44100;
            const float duration = 0.18f;
            int sampleCount = Mathf.CeilToInt(sampleRate * duration);
            var samples = new float[sampleCount];
            float startFrequency = starting ? 440f : 780f;
            float endFrequency = starting ? 880f : 330f;
            float phase = 0f;

            for (int i = 0; i < sampleCount; ++i)
            {
                float progress = i / (float)(sampleCount - 1);
                float frequency = Mathf.Lerp(startFrequency, endFrequency, progress);
                phase += 2f * Mathf.PI * frequency / sampleRate;
                float envelope = Mathf.Sin(Mathf.PI * progress);
                samples[i] = Mathf.Sin(phase) * envelope * 0.42f;
            }

            var clip = AudioClip.Create(
                starting ? "Generated Voice Gesture Start" : "Generated Voice Gesture Stop",
                sampleCount,
                1,
                sampleRate,
                false);
            clip.SetData(samples, 0);
            return clip;
        }

        void ResetVoiceGestureRecognition()
        {
            m_LeftVoiceFist = default;
            m_RightVoiceFist = default;
        }

        static bool TryGetAimPose(XRHand hand, XRHandDevice device, MetaAimHand metaAim, out Pose pose)
        {
            if (device != null && device.aimTrackingState.ReadValue() != 0)
            {
                pose = new Pose(device.aimPosition.ReadValue(), device.aimRotation.ReadValue());
                return true;
            }

            // Meta's OpenXR aim extension is needed only on Quest runtimes that do not
            // implement the standard hand interaction profile.
            if (metaAim != null && metaAim.trackingState.ReadValue() != 0)
            {
                pose = new Pose(metaAim.devicePosition.ReadValue(), metaAim.deviceRotation.ReadValue());
                return true;
            }

            if (hand.GetJoint(XRHandJointID.IndexProximal).TryGetPose(out Pose proximal) &&
                hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose tip))
            {
                Vector3 direction = (tip.position - proximal.position).normalized;
                pose = new Pose(tip.position, Quaternion.LookRotation(direction, tip.rotation * Vector3.up));
                return true;
            }

            pose = default;
            return false;
        }

        static float GetPinchAmount(XRHand hand, XRHandDevice device, MetaAimHand metaAim)
        {
            if (device != null && device.aimActivateReady.isPressed)
                return Mathf.Clamp01(device.aimActivateValue.ReadValue());
            if (device != null && device.pinchReady.isPressed)
                return Mathf.Clamp01(device.pinchValue.ReadValue());
            if (metaAim != null)
                return Mathf.Clamp01(metaAim.pinchStrengthIndex.ReadValue());

            if (hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose indexTip) &&
                hand.GetJoint(XRHandJointID.ThumbTip).TryGetPose(out Pose thumbTip))
            {
                return Mathf.InverseLerp(0.06f, 0.018f, Vector3.Distance(indexTip.position, thumbTip.position));
            }

            return 0f;
        }

        void SetHandsVisible(bool visible)
        {
            m_LeftVisual?.SetVisible(visible);
            m_RightVisual?.SetVisible(visible);
        }

        sealed class HandVisual
        {
            readonly GameObject m_Root;
            readonly Transform m_TrackingSpace;
            readonly Transform[] m_Joints = new Transform[XRHandJointID.EndMarker.ToIndex()];
            readonly Renderer[] m_Renderers = new Renderer[XRHandJointID.EndMarker.ToIndex()];
            readonly LineRenderer[] m_Bones = new LineRenderer[XRHandJointID.EndMarker.ToIndex()];
            readonly LineRenderer m_Ray;
            readonly Transform m_PinchIndicator;
            readonly Renderer m_PinchRenderer;
            readonly float m_JointSize;
            readonly Material m_HandMaterial;
            readonly Material m_PinchMaterial;

            public HandVisual(string name, Transform trackingSpace, float jointSize, Material handMaterial, Material pinchMaterial)
            {
                m_TrackingSpace = trackingSpace;
                m_JointSize = jointSize;
                m_HandMaterial = handMaterial;
                m_PinchMaterial = pinchMaterial;
                m_Root = new GameObject(name + " Visual") { hideFlags = HideFlags.DontSave };
                m_Root.transform.SetParent(trackingSpace, false);

                for (int index = 0; index < m_Joints.Length; ++index)
                {
                    XRHandJointID jointId = XRHandJointIDUtility.FromIndex(index);
                    GameObject jointObject = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                    jointObject.name = jointId.ToString();
                    jointObject.hideFlags = HideFlags.DontSave;
                    jointObject.transform.SetParent(m_Root.transform, false);
                    jointObject.transform.localScale = Vector3.one *
                        (jointId == XRHandJointID.IndexTip || jointId == XRHandJointID.ThumbTip ? jointSize * 1.35f : jointSize);

                    if (jointObject.TryGetComponent(out Collider collider))
                    {
                        collider.enabled = false;
                        Object.Destroy(collider);
                    }

                    Renderer renderer = jointObject.GetComponent<Renderer>();
                    renderer.sharedMaterial = handMaterial;
                    m_Joints[index] = jointObject.transform;
                    m_Renderers[index] = renderer;

                    if (ParentOf(jointId) == XRHandJointID.Invalid)
                        continue;

                    GameObject boneObject = new(jointId + " Bone") { hideFlags = HideFlags.DontSave };
                    boneObject.transform.SetParent(m_Root.transform, false);
                    LineRenderer bone = boneObject.AddComponent<LineRenderer>();
                    bone.sharedMaterial = handMaterial;
                    bone.useWorldSpace = true;
                    bone.positionCount = 2;
                    bone.startWidth = bone.endWidth = jointSize * 0.4f;
                    bone.numCapVertices = 4;
                    m_Bones[index] = bone;
                }

                GameObject rayObject = new("Aim Ray") { hideFlags = HideFlags.DontSave };
                rayObject.transform.SetParent(m_Root.transform, false);
                m_Ray = rayObject.AddComponent<LineRenderer>();
                m_Ray.sharedMaterial = handMaterial;
                m_Ray.useWorldSpace = true;
                m_Ray.positionCount = 2;
                m_Ray.startWidth = jointSize * 0.35f;
                m_Ray.endWidth = jointSize * 0.08f;
                m_Ray.numCapVertices = 4;

                GameObject indicator = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                indicator.name = "Pinch Indicator";
                indicator.hideFlags = HideFlags.DontSave;
                indicator.transform.SetParent(m_Root.transform, false);
                if (indicator.TryGetComponent(out Collider indicatorCollider))
                {
                    indicatorCollider.enabled = false;
                    Object.Destroy(indicatorCollider);
                }
                m_PinchIndicator = indicator.transform;
                m_PinchRenderer = indicator.GetComponent<Renderer>();
                m_PinchRenderer.sharedMaterial = pinchMaterial;

                m_Root.SetActive(false);
            }

            public void SetVisible(bool visible) => m_Root.SetActive(visible);

            public void UpdateJoints(XRHand hand, float pinchAmount, float pinchThreshold, bool touching)
            {
                for (int index = 0; index < m_Joints.Length; ++index)
                {
                    XRHandJointID jointId = XRHandJointIDUtility.FromIndex(index);
                    bool hasPose = hand.GetJoint(jointId).TryGetPose(out Pose pose);
                    m_Renderers[index].enabled = hasPose;
                    if (!hasPose)
                    {
                        if (m_Bones[index] != null)
                            m_Bones[index].enabled = false;
                        continue;
                    }

                    m_Joints[index].SetLocalPositionAndRotation(pose.position, pose.rotation);
                    XRHandJointID parentId = ParentOf(jointId);
                    if (parentId == XRHandJointID.Invalid || m_Bones[index] == null)
                        continue;

                    Transform parent = m_Joints[parentId.ToIndex()];
                    m_Bones[index].SetPosition(0, parent.position);
                    m_Bones[index].SetPosition(1, m_Joints[index].position);
                    m_Bones[index].enabled = m_Renderers[parentId.ToIndex()].enabled;
                }

                bool pinching = pinchAmount >= pinchThreshold;
                m_Renderers[XRHandJointID.IndexTip.ToIndex()].sharedMaterial = pinching || touching ? m_PinchMaterial : m_HandMaterial;
                m_Renderers[XRHandJointID.ThumbTip.ToIndex()].sharedMaterial = pinching ? m_PinchMaterial : m_HandMaterial;

                bool fingertipsTracked = m_Renderers[XRHandJointID.IndexTip.ToIndex()].enabled &&
                    m_Renderers[XRHandJointID.ThumbTip.ToIndex()].enabled;
                m_PinchRenderer.enabled = fingertipsTracked && pinchAmount > 0.1f;
                if (m_PinchRenderer.enabled)
                {
                    m_PinchIndicator.localPosition = Vector3.Lerp(
                        m_Joints[XRHandJointID.IndexTip.ToIndex()].localPosition,
                        m_Joints[XRHandJointID.ThumbTip.ToIndex()].localPosition,
                        0.5f);
                    m_PinchIndicator.localScale = Vector3.one * m_JointSize * Mathf.Lerp(1.2f, 2.6f, pinchAmount);
                }
            }

            public void UpdateRay(Pose localAimPose, float length, float pinchAmount, float pinchThreshold, Color handColor, Color pinchColor)
            {
                Vector3 origin = m_TrackingSpace.TransformPoint(localAimPose.position);
                Vector3 direction = m_TrackingSpace.TransformDirection(localAimPose.rotation * Vector3.forward).normalized;
                Color color = Color.Lerp(handColor, pinchColor, pinchAmount);

                m_Ray.SetPosition(0, origin);
                m_Ray.SetPosition(1, origin + direction * length);
                m_Ray.startColor = m_Ray.endColor = color;
                m_Ray.sharedMaterial = pinchAmount >= pinchThreshold ? m_PinchMaterial : m_HandMaterial;
                m_Ray.enabled = true;
            }

            public void HideRay() => m_Ray.enabled = false;

            public void Destroy() => Object.Destroy(m_Root);

            static XRHandJointID ParentOf(XRHandJointID jointId)
            {
                return jointId switch
                {
                    XRHandJointID.Palm => XRHandJointID.Wrist,
                    XRHandJointID.ThumbMetacarpal => XRHandJointID.Wrist,
                    XRHandJointID.ThumbProximal => XRHandJointID.ThumbMetacarpal,
                    XRHandJointID.ThumbDistal => XRHandJointID.ThumbProximal,
                    XRHandJointID.ThumbTip => XRHandJointID.ThumbDistal,
                    XRHandJointID.IndexMetacarpal => XRHandJointID.Wrist,
                    XRHandJointID.IndexProximal => XRHandJointID.IndexMetacarpal,
                    XRHandJointID.IndexIntermediate => XRHandJointID.IndexProximal,
                    XRHandJointID.IndexDistal => XRHandJointID.IndexIntermediate,
                    XRHandJointID.IndexTip => XRHandJointID.IndexDistal,
                    XRHandJointID.MiddleMetacarpal => XRHandJointID.Wrist,
                    XRHandJointID.MiddleProximal => XRHandJointID.MiddleMetacarpal,
                    XRHandJointID.MiddleIntermediate => XRHandJointID.MiddleProximal,
                    XRHandJointID.MiddleDistal => XRHandJointID.MiddleIntermediate,
                    XRHandJointID.MiddleTip => XRHandJointID.MiddleDistal,
                    XRHandJointID.RingMetacarpal => XRHandJointID.Wrist,
                    XRHandJointID.RingProximal => XRHandJointID.RingMetacarpal,
                    XRHandJointID.RingIntermediate => XRHandJointID.RingProximal,
                    XRHandJointID.RingDistal => XRHandJointID.RingIntermediate,
                    XRHandJointID.RingTip => XRHandJointID.RingDistal,
                    XRHandJointID.LittleMetacarpal => XRHandJointID.Wrist,
                    XRHandJointID.LittleProximal => XRHandJointID.LittleMetacarpal,
                    XRHandJointID.LittleIntermediate => XRHandJointID.LittleProximal,
                    XRHandJointID.LittleDistal => XRHandJointID.LittleIntermediate,
                    XRHandJointID.LittleTip => XRHandJointID.LittleDistal,
                    _ => XRHandJointID.Invalid,
                };
            }
        }
    }
}
