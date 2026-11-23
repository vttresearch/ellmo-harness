using System.Collections;
using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;
using UnityEngine.XR.OpenXR.Features.Meta;

#if UNITY_ANDROID
using UnityEngine.Android;
#endif

namespace Ellmo.XR.CameraFeed
{
    /// <summary>
    /// Quest implementation backed by Unity Meta OpenXR's AR camera subsystem.
    /// That subsystem currently exposes the left world-facing camera as a CPU image.
    /// </summary>
    public sealed class MetaQuest3CameraFeed : MonoBehaviour, IXRCameraFeed
    {
        const string k_HeadsetCameraPermission = "horizonos.permission.HEADSET_CAMERA";

        [SerializeField] ARCameraManager cameraManager;
        [SerializeField, Range(1f, 30f)] float targetFramesPerSecond = 15f;
        [SerializeField, Min(64)] int maximumTextureWidth = 1280;

        Texture2D m_Texture;
        XRCpuImage.AsyncConversion m_Conversion;
        bool m_ConversionInFlight;
        bool m_FeedRequested;
        bool m_PermissionRequestPending;
        bool m_CameraManagerEnabledBeforeFeed;
        float m_NextCaptureTime;
        float m_StartupDeadline;

        public string SourceId => "meta_quest_left_passthrough_camera";
        public Texture Texture => m_Texture;
        public Vector2Int Resolution => m_Texture == null
            ? Vector2Int.zero
            : new Vector2Int(m_Texture.width, m_Texture.height);
        public XRCameraFeedState State { get; private set; } = XRCameraFeedState.Stopped;
        public string Status { get; private set; } = "Stopped";

        void Awake() => ResolveCameraManager();

        void Update()
        {
            if (!m_FeedRequested || State == XRCameraFeedState.RequestingPermission ||
                State == XRCameraFeedState.PermissionDenied || State == XRCameraFeedState.Unsupported)
                return;

            if (cameraManager == null)
            {
                SetState(XRCameraFeedState.Error, "No AR Camera Manager is configured.");
                return;
            }

            if (cameraManager.subsystem == null || !cameraManager.subsystem.running)
            {
                if (Time.unscaledTime > m_StartupDeadline)
                    SetState(XRCameraFeedState.Error, "The Meta OpenXR camera subsystem did not start.");
                return;
            }

            if (cameraManager.subsystem is not MetaOpenXRCameraSubsystem)
            {
                SetState(XRCameraFeedState.Unsupported, "The active camera provider is not Unity Meta OpenXR.");
                return;
            }

            if (m_ConversionInFlight || Time.unscaledTime < m_NextCaptureTime)
                return;

            m_NextCaptureTime = Time.unscaledTime + 1f / targetFramesPerSecond;
            CaptureNextFrame();
        }

        public void StartFeed()
        {
            if (m_FeedRequested)
                return;

            m_FeedRequested = true;
            ResolveCameraManager();
            m_CameraManagerEnabledBeforeFeed = cameraManager != null && cameraManager.enabled;

#if UNITY_ANDROID && !UNITY_EDITOR
            if (Permission.HasUserAuthorizedPermission(k_HeadsetCameraPermission))
            {
                StartCameraSubsystem();
                return;
            }

            RequestCameraPermission();
#else
            SetState(XRCameraFeedState.Unsupported, "Quest camera capture is available only in an Android player.");
#endif
        }

        public void StopFeed()
        {
            m_FeedRequested = false;
            m_PermissionRequestPending = false;
            ReleaseConversion();
            if (cameraManager != null)
                cameraManager.enabled = m_CameraManagerEnabledBeforeFeed;
            SetState(XRCameraFeedState.Stopped, "Stopped");
        }

        void OnDestroy()
        {
            ReleaseConversion();
            if (m_Texture != null)
                Destroy(m_Texture);
        }

        void ResolveCameraManager()
        {
            if (cameraManager == null)
                cameraManager = FindFirstObjectByType<ARCameraManager>();
        }

#if UNITY_ANDROID
        void RequestCameraPermission()
        {
            if (m_PermissionRequestPending)
                return;

            m_PermissionRequestPending = true;
            SetState(XRCameraFeedState.RequestingPermission, "Waiting for headset camera permission.");

            // Starting the provider before the permission result can leave it in an
            // uninitialized state, so restart it after the user grants access.
            if (cameraManager != null)
                cameraManager.enabled = false;

            var callbacks = new PermissionCallbacks();
            callbacks.PermissionGranted += OnPermissionGranted;
            callbacks.PermissionDenied += OnPermissionDenied;
            Permission.RequestUserPermission(k_HeadsetCameraPermission, callbacks);
        }

        void OnPermissionGranted(string permission)
        {
            m_PermissionRequestPending = false;
            if (m_FeedRequested)
                StartCameraSubsystem();
        }

        void OnPermissionDenied(string permission)
        {
            m_PermissionRequestPending = false;
            if (cameraManager != null)
                cameraManager.enabled = m_CameraManagerEnabledBeforeFeed;
            SetState(XRCameraFeedState.PermissionDenied, "Headset camera permission was denied.");
        }
#endif

        void StartCameraSubsystem()
        {
            if (cameraManager == null)
            {
                SetState(XRCameraFeedState.Error, "No AR Camera Manager is configured.");
                return;
            }

            // Re-enabling initializes the native Quest camera after permission is available.
            cameraManager.enabled = false;
            cameraManager.enabled = true;
            m_StartupDeadline = Time.unscaledTime + 8f;
            m_NextCaptureTime = 0f;
            SetState(XRCameraFeedState.Starting, "Starting the Quest camera feed.");
        }

        void CaptureNextFrame()
        {
            if (!cameraManager.TryAcquireLatestCpuImage(out XRCpuImage image))
            {
                if (m_Texture == null && Time.unscaledTime > m_StartupDeadline)
                    SetState(XRCameraFeedState.Error, "The Meta OpenXR camera started but did not provide an image.");
                return;
            }

            Vector2Int outputDimensions = FitWithinMaximumWidth(image.dimensions, maximumTextureWidth);
            var conversionParams = new XRCpuImage.ConversionParams
            {
                inputRect = new RectInt(0, 0, image.width, image.height),
                outputDimensions = outputDimensions,
                outputFormat = TextureFormat.RGBA32,
                // Quest's CPU image arrives vertically inverted for Unity's quad UVs.
                transformation = XRCpuImage.Transformation.MirrorX,
            };

            try
            {
                m_Conversion = image.ConvertAsync(conversionParams);
                m_ConversionInFlight = true;
            }
            finally
            {
                image.Dispose();
            }

            StartCoroutine(CompleteConversion());
        }

        IEnumerator CompleteConversion()
        {
            while (m_ConversionInFlight && !m_Conversion.status.IsDone())
                yield return null;

            if (!m_ConversionInFlight)
                yield break;

            try
            {
                if (m_Conversion.status != XRCpuImage.AsyncConversionStatus.Ready)
                {
                    SetState(XRCameraFeedState.Error, $"Camera image conversion failed: {m_Conversion.status}.");
                    yield break;
                }

                Vector2Int size = m_Conversion.conversionParams.outputDimensions;
                if (m_Texture == null || m_Texture.width != size.x || m_Texture.height != size.y)
                {
                    if (m_Texture != null)
                        Destroy(m_Texture);

                    m_Texture = new Texture2D(size.x, size.y, TextureFormat.RGBA32, false)
                    {
                        name = "Meta Quest 3 Camera Feed",
                        filterMode = FilterMode.Bilinear,
                        wrapMode = TextureWrapMode.Clamp,
                    };
                }

                m_Texture.LoadRawTextureData(m_Conversion.GetData<byte>());
                m_Texture.Apply(false, false);
                SetState(XRCameraFeedState.Streaming, $"Streaming {size.x} x {size.y} from the Quest left camera.");
            }
            finally
            {
                ReleaseConversion();
            }
        }

        void ReleaseConversion()
        {
            if (!m_ConversionInFlight)
                return;

            m_Conversion.Dispose();
            m_ConversionInFlight = false;
        }

        void SetState(XRCameraFeedState state, string status)
        {
            State = state;
            Status = status;
        }

        static Vector2Int FitWithinMaximumWidth(Vector2Int source, int maximumWidth)
        {
            if (source.x <= maximumWidth)
                return source;

            float scale = maximumWidth / (float)source.x;
            int height = Mathf.Max(2, Mathf.RoundToInt(source.y * scale));
            return new Vector2Int(maximumWidth, height);
        }
    }
}
