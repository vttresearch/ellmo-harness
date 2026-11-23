using System;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using PDFtoImage;
using SkiaSharp;
using UnityEngine;
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
using PdfiumLength = System.UInt32;
#else
using PdfiumLength = System.UIntPtr;
#endif

namespace UnityPdfViewer
{
    public static class PdfLoader
    {
        // Use the PDFium already bundled by PDFtoImage. Its page-label API returns
        // UTF-16LE and uses C unsigned long (32-bit on Windows, native-width on Unix).
        [DllImport("pdfium")]
        static extern IntPtr FPDF_LoadMemDocument(IntPtr data, int size, string password);
        [DllImport("pdfium")]
        static extern void FPDF_CloseDocument(IntPtr document);
        [DllImport("pdfium")]
        static extern PdfiumLength FPDF_GetPageLabel(
            IntPtr document, int pageIndex, [Out] byte[] buffer, PdfiumLength length);

        public static string[] GetPageLabels(byte[] pdfData, int pageCount)
        {
            // GetPageCount initializes PDFtoImage/PDFium before this method is called.
            GCHandle pinned = GCHandle.Alloc(pdfData, GCHandleType.Pinned);
            IntPtr document = IntPtr.Zero;
            try
            {
                document = FPDF_LoadMemDocument(pinned.AddrOfPinnedObject(), pdfData.Length, null);
                if (document == IntPtr.Zero)
                    throw new InvalidOperationException("Unable to open PDF page labels.");
                string[] labels = new string[pageCount];
                for (int index = 0; index < pageCount; index++)
                {
                    var length = FPDF_GetPageLabel(document, index, null, default);
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
                    int byteCount = checked((int)length);
#else
                    int byteCount = checked((int)length.ToUInt64());
#endif
                    string label = "";
                    if (byteCount > 2)
                    {
                        byte[] buffer = new byte[byteCount];
                        FPDF_GetPageLabel(document, index, buffer, length);
                        label = Encoding.Unicode.GetString(buffer, 0, byteCount - 2);
                    }
                    labels[index] = string.IsNullOrEmpty(label)
                        ? (index + 1).ToString(CultureInfo.InvariantCulture) : label;
                }
                return labels;
            }
            finally
            {
                if (document != IntPtr.Zero)
                    FPDF_CloseDocument(document);
                pinned.Free();
            }
        }

        public static int GetPageCount(byte[] pdfData)
        {
            if (pdfData == null || pdfData.Length == 0)
                return 0;

            try
            {
                return Conversion.GetPageCount(pdfData, null);
            }
            catch (Exception exception)
            {
                Debug.LogError($"Unable to read PDF: {exception.Message}");
                return 0;
            }
        }

        public static Texture2D LoadPage(byte[] pdfData, int pageIndex, int dpi)
        {
            if (pdfData == null || pdfData.Length == 0)
                return null;

            try
            {
                using SKBitmap bitmap = Conversion.ToImage(
                    pdfData,
                    new Index(pageIndex),
                    null,
                    new RenderOptions { Dpi = dpi });
                return ConvertToTexture(bitmap, pageIndex);
            }
            catch (Exception exception)
            {
                Debug.LogError($"Unable to render PDF page {pageIndex + 1}: {exception.Message}");
                return null;
            }
        }

        static Texture2D ConvertToTexture(SKBitmap bitmap, int pageIndex)
        {
            if (bitmap == null)
                return null;

            int width = bitmap.Width;
            int height = bitmap.Height;
            SKColor[] source = bitmap.Pixels;
            Color32[] pixels = new Color32[source.Length];

            for (int y = 0; y < height; ++y)
            {
                int sourceRow = y * width;
                int destinationRow = (height - 1 - y) * width;
                for (int x = 0; x < width; ++x)
                {
                    SKColor color = source[sourceRow + x];
                    pixels[destinationRow + x] = new Color32(color.Red, color.Green, color.Blue, color.Alpha);
                }
            }

            Texture2D texture = new(width, height, TextureFormat.RGBA32, false)
            {
                name = $"PDF Page {pageIndex + 1}",
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp,
            };
            texture.SetPixels32(pixels);
            texture.Apply(false, true);
            return texture;
        }
    }
}
