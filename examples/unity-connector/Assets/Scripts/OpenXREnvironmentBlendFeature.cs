using UnityEngine.XR.OpenXR.Features;
using UnityEngine.XR.OpenXR.NativeTypes;

#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.XR.OpenXR.Features;
#endif

namespace Ellmo.XR
{
#if UNITY_EDITOR
    [OpenXRFeature(
        UiName = "Environment Passthrough (Alpha Blend)",
        Desc = "Requests OpenXR alpha-blend environment passthrough on runtimes that support it, such as Varjo.",
        Company = "Ellmo",
        Version = "1.0.0",
        BuildTargetGroups = new[] { BuildTargetGroup.Standalone },
        FeatureId = FeatureId)]
#endif
    public sealed class OpenXREnvironmentBlendFeature : OpenXRFeature
    {
        public const string FeatureId = "com.ellmo.openxr.feature.environment-alpha-blend";

        protected override void OnSessionBegin(ulong xrSession)
        {
            SetEnvironmentBlendMode(XrEnvironmentBlendMode.AlphaBlend);
        }
    }
}
