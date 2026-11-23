using UnityEngine;

namespace UnityPdfViewer
{
    public sealed class PdfNavigator
    {
        public int CurrentPage { get; private set; }
        public int TotalPages { get; }

        public PdfNavigator(int totalPages)
        {
            TotalPages = Mathf.Max(0, totalPages);
        }

        public void Next()
        {
            if (TotalPages > 0)
                CurrentPage = Mathf.Min(CurrentPage + 1, TotalPages - 1);
        }

        public void Previous()
        {
            if (TotalPages > 0)
                CurrentPage = Mathf.Max(CurrentPage - 1, 0);
        }

        public void GoTo(int pageIndex)
        {
            if (TotalPages > 0)
                CurrentPage = Mathf.Clamp(pageIndex, 0, TotalPages - 1);
        }
    }
}
