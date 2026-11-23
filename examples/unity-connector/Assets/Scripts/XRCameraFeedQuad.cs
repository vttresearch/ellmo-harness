using UnityEngine;

namespace Ellmo.XR.CameraFeed
{
    /// <summary>
    /// Platform-neutral presentation of an IXRCameraFeed on a scene quad.
    /// </summary>
    public sealed class XRCameraFeedQuad : MonoBehaviour
    {
        static readonly int s_BaseMap = Shader.PropertyToID("_BaseMap");
        static readonly int s_MainTex = Shader.PropertyToID("_MainTex");

        [SerializeField] MonoBehaviour feedSource;
        [SerializeField] Renderer targetRenderer;
        [SerializeField, Min(0.05f)] float widthMetres = 0.5f;

        IXRCameraFeed m_Feed;
        MaterialPropertyBlock m_Properties;
        Texture m_AppliedTexture;
        XRCameraFeedState m_LastReportedState = XRCameraFeedState.Stopped;
        bool m_Started;

        void Awake()
        {
            m_Feed = feedSource as IXRCameraFeed;
            if (targetRenderer == null)
                targetRenderer = GetComponent<Renderer>();
            m_Properties = new MaterialPropertyBlock();
        }

        void Start()
        {
            m_Started = true;
            m_Feed?.StartFeed();
        }

        void OnEnable()
        {
            if (m_Started)
                m_Feed?.StartFeed();
        }

        void OnDisable()
        {
            if (m_Started)
                m_Feed?.StopFeed();
        }

        void Update()
        {
            if (m_Feed == null)
                return;

            ReportTerminalStateOnce();

            Texture texture = m_Feed.Texture;
            if (texture == null || texture == m_AppliedTexture || targetRenderer == null)
                return;

            m_AppliedTexture = texture;
            targetRenderer.GetPropertyBlock(m_Properties);
            m_Properties.SetTexture(s_BaseMap, texture);
            m_Properties.SetTexture(s_MainTex, texture);
            m_Properties.SetColor("_BaseColor", Color.white);
            m_Properties.SetColor("_Color", Color.white);
            targetRenderer.SetPropertyBlock(m_Properties);

            float aspect = texture.width / (float)texture.height;
            transform.localScale = new Vector3(widthMetres, widthMetres / aspect, 1f);
        }

        void ReportTerminalStateOnce()
        {
            if (m_LastReportedState == m_Feed.State)
                return;

            m_LastReportedState = m_Feed.State;
            if (m_Feed.State is XRCameraFeedState.PermissionDenied or XRCameraFeedState.Unsupported or XRCameraFeedState.Error)
                Debug.LogWarning($"[Ellmo XR Camera] {m_Feed.Status}", this);
        }
    }
}
