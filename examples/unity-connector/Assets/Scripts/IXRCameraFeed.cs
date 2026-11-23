using UnityEngine;

namespace Ellmo.XR.CameraFeed
{
    public enum XRCameraFeedState
    {
        Stopped,
        RequestingPermission,
        Starting,
        Streaming,
        PermissionDenied,
        Unsupported,
        Error,
    }

    /// <summary>
    /// Platform-neutral camera feed consumed by scene presentation code.
    /// A future Varjo implementation can replace the source component without
    /// changing the quad or its material.
    /// </summary>
    public interface IXRCameraFeed
    {
        string SourceId { get; }
        Texture Texture { get; }
        Vector2Int Resolution { get; }
        XRCameraFeedState State { get; }
        string Status { get; }

        void StartFeed();
        void StopFeed();
    }
}
