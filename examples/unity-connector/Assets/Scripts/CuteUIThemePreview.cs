using UnityEngine;

/// <summary>
/// Keeps the shared rounded UI treatment visible while editing either scene.
/// Play mode is still initialized by ChatExample so runtime-created controls
/// receive the same theme as the persistent canvas controls.
/// </summary>
[ExecuteAlways]
[DisallowMultipleComponent]
public sealed class CuteUIThemePreview : MonoBehaviour
{
#if UNITY_EDITOR
    private bool _refreshQueued;

    private void OnEnable()
    {
        QueueRefresh();
    }

    private void OnValidate()
    {
        QueueRefresh();
    }

    private void OnTransformChildrenChanged()
    {
        QueueRefresh();
    }

    private void QueueRefresh()
    {
        if (Application.isPlaying || _refreshQueued)
            return;

        _refreshQueued = true;
        UnityEditor.EditorApplication.delayCall += Refresh;
    }

    private void Refresh()
    {
        _refreshQueued = false;
        if (this == null || Application.isPlaying || !isActiveAndEnabled)
            return;

        CuteUITheme.ApplyToHierarchy(transform);
        UnityEditor.SceneView.RepaintAll();
    }
#endif
}
