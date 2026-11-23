using System;
using System.Collections;
using System.Globalization;
using System.IO;
using TMPro;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace UnityPdfViewer
{
    public sealed class PdfViewerUI : MonoBehaviour
    {
        [Header("UI References")]
        public RawImage pdfImage;
        public TMP_Text pageIndicator;
        public Button nextButton;
        public Button previousButton;

        [Range(72, 200)] public int renderDPI = 110;

        [HideInInspector] public PdfNavigator navigator;

        byte[] m_PdfData;
        string[] m_PageLabels;
        int m_RenderedPageIndex = -1;
        Texture2D m_CurrentPageTexture;
        Coroutine m_LoadRoutine;
        TMP_Text m_UnavailableHint;

        void Start()
        {
            nextButton?.onClick.AddListener(NextPage);
            previousButton?.onClick.AddListener(PreviousPage);
        }

        public void LoadPDF(string pdfFileName)
        {
            if (m_LoadRoutine != null)
                StopCoroutine(m_LoadRoutine);

            ReleaseDocument();
            m_LoadRoutine = StartCoroutine(LoadDocument(pdfFileName));
        }

        IEnumerator LoadDocument(string pdfFileName)
        {
            SetLoadingState("Loading PDF...");
            string path = Path.Combine(Application.streamingAssetsPath, pdfFileName);

            if (path.Contains("://"))
            {
                using UnityWebRequest request = UnityWebRequest.Get(path);
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    ReportLoadError($"PDF could not be loaded from StreamingAssets: {request.error}");
                    yield break;
                }

                m_PdfData = request.downloadHandler.data;
            }
            else
            {
                if (!File.Exists(path))
                {
                    ReportLoadError($"Optional PDF file not found: {path}", missingFile: true);
                    yield break;
                }

                m_PdfData = File.ReadAllBytes(path);
            }

            // Do not block XR initialization on the same frame as scene startup.
            yield return null;

            int pageCount = PdfLoader.GetPageCount(m_PdfData);
            if (pageCount == 0)
            {
                ReportLoadError($"PDF contains no readable pages: {pdfFileName}");
                yield break;
            }

            try
            {
                m_PageLabels = PdfLoader.GetPageLabels(m_PdfData, pageCount);
            }
            catch (Exception exception)
            {
                ReportLoadError($"PDF page labels could not be read: {exception.Message}");
                yield break;
            }
            navigator = new PdfNavigator(pageCount);
            RenderCurrentPage();
            m_LoadRoutine = null;
            Debug.Log($"[PDF Viewer] Loaded '{pdfFileName}' ({pageCount} pages); pages render on demand.");
        }

        public void NextPage()
        {
            if (navigator == null)
                return;

            int previousPage = navigator.CurrentPage;
            navigator.Next();
            if (navigator.CurrentPage != previousPage)
                RenderCurrentPage();
        }

        public void PreviousPage()
        {
            if (navigator == null)
                return;

            int previousPage = navigator.CurrentPage;
            navigator.Previous();
            if (navigator.CurrentPage != previousPage)
                RenderCurrentPage();
        }

        // Public page numbers follow the PDF's printed labels, not file positions.
        public void GoToPage(int pageNumber)
        {
            GoToPageLabel(pageNumber.ToString(CultureInfo.InvariantCulture));
        }

        public bool GoToPageLabel(string pageLabel)
        {
            if (navigator == null || m_PageLabels == null)
                return false;

            int index = Array.IndexOf(m_PageLabels, pageLabel);
            // Duplicate labels cannot identify one page unambiguously.
            if (index < 0 || Array.LastIndexOf(m_PageLabels, pageLabel) != index)
                return false;

            int previousPage = navigator.CurrentPage;
            navigator.GoTo(index);
            if (navigator.CurrentPage != previousPage || m_RenderedPageIndex != index)
                RenderCurrentPage();
            return m_RenderedPageIndex == index;
        }

        public int GetTotalPages() => navigator?.TotalPages ?? 0;
        public int GetCurrentPdfPage() => m_RenderedPageIndex + 1;
        public string GetCurrentPageLabel() => m_RenderedPageIndex >= 0
            ? m_PageLabels[m_RenderedPageIndex] : null;
        public bool IsCurrentPageRendered() => navigator != null
            && m_RenderedPageIndex == navigator.CurrentPage;

        void RenderCurrentPage()
        {
            if (navigator == null || pdfImage == null)
                return;

            Texture2D nextTexture = PdfLoader.LoadPage(m_PdfData, navigator.CurrentPage, renderDPI);
            if (nextTexture == null)
                return;

            Texture2D previousTexture = m_CurrentPageTexture;
            m_CurrentPageTexture = nextTexture;
            m_RenderedPageIndex = navigator.CurrentPage;
            pdfImage.texture = nextTexture;
            if (previousTexture != null)
                Destroy(previousTexture);

            UpdateControls();
        }

        void UpdateControls()
        {
            if (navigator == null)
                return;

            if (pageIndicator != null)
                pageIndicator.text = $"Page {GetCurrentPageLabel()} (PDF {GetCurrentPdfPage()} / {navigator.TotalPages})";
            if (nextButton != null)
                nextButton.interactable = navigator.CurrentPage < navigator.TotalPages - 1;
            if (previousButton != null)
                previousButton.interactable = navigator.CurrentPage > 0;
        }

        void SetLoadingState(string message)
        {
            if (pageIndicator != null)
                pageIndicator.text = message;
            if (nextButton != null)
                nextButton.interactable = false;
            if (previousButton != null)
                previousButton.interactable = false;
        }

        void ReportLoadError(string message, bool missingFile = false)
        {
            m_LoadRoutine = null;
            SetLoadingState("PDF unavailable");
            ShowUnavailableHint();
            if (missingFile)
                Debug.LogWarning($"[PDF Viewer] {message}");
            else
                Debug.LogError($"[PDF Viewer] {message}");
        }

        void ShowUnavailableHint()
        {
            if (pdfImage == null)
                return;

            if (m_UnavailableHint == null)
            {
                GameObject hint = new("PDF Setup Hint", typeof(RectTransform));
                hint.layer = pdfImage.gameObject.layer;
                RectTransform rect = (RectTransform)hint.transform;
                rect.SetParent(pdfImage.transform, false);
                rect.anchorMin = new Vector2(0.08f, 0.08f);
                rect.anchorMax = new Vector2(0.92f, 0.92f);
                rect.offsetMin = Vector2.zero;
                rect.offsetMax = Vector2.zero;

                m_UnavailableHint = hint.AddComponent<TextMeshProUGUI>();
                m_UnavailableHint.color = Color.black;
                m_UnavailableHint.alignment = TextAlignmentOptions.Center;
                m_UnavailableHint.enableAutoSizing = true;
                // Desktop and XR use different canvas scales.
                m_UnavailableHint.fontSizeMin = 0.5f;
                m_UnavailableHint.fontSizeMax = 28f;
                m_UnavailableHint.textWrappingMode = TextWrappingModes.Normal;
                m_UnavailableHint.raycastTarget = false;
                m_UnavailableHint.text = "<b>PDF unavailable</b>\n\n" +
                    "Copy the PDF used for backend queries into this Unity project's " +
                    "Assets/StreamingAssets folder.\n\n" +
                    "For Siemens, use the copy in backend/tools/query_sinamics_docs/ " +
                    "or the official download linked in docs/setup.md.\n\n" +
                    "Keep the same filename and edition, then restart the scene.";
            }

            m_UnavailableHint.gameObject.SetActive(true);
        }

        void ReleaseDocument()
        {
            if (m_UnavailableHint != null)
                m_UnavailableHint.gameObject.SetActive(false);
            navigator = null;
            m_PdfData = null;
            m_PageLabels = null;
            m_RenderedPageIndex = -1;
            if (m_CurrentPageTexture != null)
            {
                Destroy(m_CurrentPageTexture);
                m_CurrentPageTexture = null;
            }
            if (pdfImage != null)
                pdfImage.texture = null;
        }

        void OnDestroy()
        {
            nextButton?.onClick.RemoveListener(NextPage);
            previousButton?.onClick.RemoveListener(PreviousPage);
            ReleaseDocument();
            if (m_UnavailableHint != null)
                Destroy(m_UnavailableHint.gameObject);
        }
    }
}
