using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Shared, asset-free rounded styling for the desktop and OpenXR canvases.
/// The generated nine-slice keeps corners crisp at every canvas scale without
/// requiring platform-specific shaders or duplicated scene assets.
/// </summary>
public static class CuteUITheme
{
    public const float PanelCornerRadius = 24f;
    public const float BubbleCornerRadius = 18f;
    public const float ControlCornerRadius = 14f;
    public const float CompactCornerRadius = 8f;

    private const int SpriteSize = 64;

    private static readonly Dictionary<int, Sprite> RoundedSprites = new();

#if UNITY_EDITOR
    private static Sprite EditorPreviewSprite;
    private static bool EditorPreviewSpriteLookupComplete;
#endif

    private static readonly HashSet<string> SurfaceNames = new()
    {
        "Bubble",
        "Panel",
        "Log Panel",
        "Chat Scroll View",
        "Template",
        "Context window fill",
        "Prompt Fill",
        "Total Fill",
    };

    private static readonly HashSet<string> IconOrMaskNames = new()
    {
        "Arrow",
        "Item Checkmark",
        "Viewport",
    };

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    private static void ResetCache()
    {
        RoundedSprites.Clear();
    }

    /// <summary>
    /// Applies the theme to the persistent controls and surfaces under a canvas.
    /// Runtime-created UI should call Apply directly when it is constructed.
    /// </summary>
    public static void ApplyToHierarchy(Transform root)
    {
        if (root == null)
            return;

        foreach (var image in root.GetComponentsInChildren<Image>(includeInactive: true))
        {
            if (!ShouldRound(image))
                continue;

            Apply(image, RadiusFor(image));
        }
    }

    public static void Apply(Image image, float cornerRadius = ControlCornerRadius)
    {
        if (image == null)
            return;

        image.sprite = GetRoundedSprite(image, cornerRadius, out var pixelsPerUnitMultiplier);
        image.type = Image.Type.Sliced;
        image.fillCenter = true;
        image.preserveAspect = false;
        image.pixelsPerUnitMultiplier = pixelsPerUnitMultiplier;
    }

    private static bool ShouldRound(Image image)
    {
        if (image == null || IconOrMaskNames.Contains(image.gameObject.name))
            return false;

        if (SurfaceNames.Contains(image.gameObject.name))
            return true;

        // This includes button backgrounds, input/dropdown backgrounds, toggle
        // item backgrounds, scrollbar tracks, and their draggable handles.
        foreach (var selectable in image.GetComponentsInParent<Selectable>(includeInactive: true))
        {
            if (selectable.targetGraphic == image)
                return true;
        }

        return false;
    }

    private static float RadiusFor(Image image)
    {
        var objectName = image.gameObject.name;
        if (objectName == "Bubble")
            return BubbleCornerRadius;

        if (objectName == "Panel" || objectName == "Log Panel" || objectName == "Chat Scroll View" || objectName == "Template")
            return PanelCornerRadius;

        if (objectName == "Context window fill" || objectName == "Prompt Fill" || objectName == "Total Fill" ||
            objectName == "Scrollbar" || objectName == "Scrollbar Vertical" || objectName == "Handle")
            return CompactCornerRadius;

        return ControlCornerRadius;
    }

    private static Sprite GetRoundedSprite(Image image, float requestedRadius, out float pixelsPerUnitMultiplier)
    {
        var radius = Mathf.Clamp(Mathf.RoundToInt(requestedRadius), 2, SpriteSize / 2 - 2);

#if UNITY_EDITOR
        // A real, persistent built-in sprite makes the same rounded treatment
        // visible in Scene and Game views before play mode. Runtime continues to
        // use the smoother procedural sprite below.
        if (!Application.isPlaying)
        {
            if (!EditorPreviewSpriteLookupComplete)
            {
                EditorPreviewSprite = UnityEditor.AssetDatabase.GetBuiltinExtraResource<Sprite>("UI/Skin/UISprite.psd");
                EditorPreviewSpriteLookupComplete = true;
            }

            var previewSprite = EditorPreviewSprite;
            if (previewSprite != null)
            {
                var referencePixelsPerUnit = image.canvas != null ? image.canvas.referencePixelsPerUnit : 100f;
                var sourceRadius = Mathf.Min(previewSprite.border.x, previewSprite.border.y) *
                                   referencePixelsPerUnit / previewSprite.pixelsPerUnit;
                pixelsPerUnitMultiplier = Mathf.Max(0.01f, sourceRadius / radius);
                return previewSprite;
            }
        }
#endif

        pixelsPerUnitMultiplier = 1f;
        if (RoundedSprites.TryGetValue(radius, out var cachedSprite) && cachedSprite != null)
            return cachedSprite;

        var texture = new Texture2D(SpriteSize, SpriteSize, TextureFormat.RGBA32, mipChain: false, linear: true)
        {
            name = $"Cute UI Rounded Rectangle {radius}px",
            filterMode = FilterMode.Bilinear,
            wrapMode = TextureWrapMode.Clamp,
            hideFlags = HideFlags.HideAndDontSave,
        };

        var pixels = new Color32[SpriteSize * SpriteSize];
        var halfSize = SpriteSize * 0.5f;
        var innerHalfSize = halfSize - radius;

        for (var y = 0; y < SpriteSize; y++)
        {
            for (var x = 0; x < SpriteSize; x++)
            {
                // Signed distance to a rounded box. The half-pixel offset samples
                // pixel centers and the final one-pixel ramp anti-aliases corners.
                var px = Mathf.Abs(x + 0.5f - halfSize) - innerHalfSize;
                var py = Mathf.Abs(y + 0.5f - halfSize) - innerHalfSize;
                var outsideDistance = new Vector2(Mathf.Max(px, 0f), Mathf.Max(py, 0f)).magnitude;
                var insideDistance = Mathf.Min(Mathf.Max(px, py), 0f);
                var signedDistance = outsideDistance + insideDistance - radius;
                var alpha = (byte)Mathf.RoundToInt(Mathf.Clamp01(0.5f - signedDistance) * 255f);
                pixels[y * SpriteSize + x] = new Color32(255, 255, 255, alpha);
            }
        }

        texture.SetPixels32(pixels);
        texture.Apply(updateMipmaps: false, makeNoLongerReadable: true);

        var border = new Vector4(radius, radius, radius, radius);
        var sprite = Sprite.Create(
            texture,
            new Rect(0f, 0f, SpriteSize, SpriteSize),
            new Vector2(0.5f, 0.5f),
            100f,
            0,
            SpriteMeshType.FullRect,
            border);
        sprite.name = texture.name;
        sprite.hideFlags = HideFlags.HideAndDontSave;

        RoundedSprites[radius] = sprite;
        return sprite;
    }
}
