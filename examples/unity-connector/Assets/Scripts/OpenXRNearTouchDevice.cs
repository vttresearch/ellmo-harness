using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.Layouts;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.InputSystem.Utilities;
using UnityEngine.InputSystem.XR;

namespace Ellmo.XR
{
    [StructLayout(LayoutKind.Explicit, Size = 40)]
    struct OpenXRNearTouchState : IInputStateTypeInfo
    {
        public FourCC format => new('E', 'U', 'I', 'T');

        [FieldOffset(0), InputControl(name = "trackingState", layout = "Integer")]
        public int trackingState;

        [FieldOffset(4), InputControl(name = "isTracked", layout = "Button")]
        public byte isTracked;

        [FieldOffset(5), InputControl(name = "touch", layout = "Button")]
        public byte touch;

        [FieldOffset(8), InputControl(name = "devicePosition", layout = "Vector3", noisy = true)]
        public Vector3 position;

        [FieldOffset(20), InputControl(name = "deviceRotation", layout = "Quaternion", noisy = true)]
        public Quaternion rotation;
    }

    /// <summary>
    /// A small virtual tracked device lets InputSystemUIInputModule handle fingertip
    /// hover, press, click and drag with exactly the same path as other XR pointers.
    /// </summary>
    [InputControlLayout(stateType = typeof(OpenXRNearTouchState), displayName = "OpenXR Near Touch")]
    sealed class OpenXRNearTouchDevice : TrackedDevice
    {
        public ButtonControl touch { get; private set; }

        protected override void FinishSetup()
        {
            base.FinishSetup();
            touch = GetChildControl<ButtonControl>("touch");
        }
    }
}
