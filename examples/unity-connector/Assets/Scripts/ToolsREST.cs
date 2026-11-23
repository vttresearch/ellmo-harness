using System;
using System.Collections;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Networking;

public class ToolsREST : MonoBehaviour
{
    private string _restBaseUrl;
    private Action<JToken> _onLogMessage;
    private int _endpointVersion;

    public void Initialize(string restBaseUrl, Action<JToken> onLogMessage)
    {
        if (!string.Equals(_restBaseUrl, restBaseUrl, StringComparison.Ordinal))
            _endpointVersion++;

        _restBaseUrl = restBaseUrl;
        _onLogMessage = onLogMessage;
    }

    private static JToken ToToken(string text)
    {
        if (string.IsNullOrEmpty(text))
            return JValue.CreateString(string.Empty);

        try
        {
            return JToken.Parse(text);
        }
        catch
        {
            return JValue.CreateString(text);
        }
    }

    public void CheckHealth()
    {
        StartCoroutine(CheckHealthCoroutine(_endpointVersion));
    }

    public void FetchMachines()
    {
        StartCoroutine(FetchMachinesCoroutine(_endpointVersion));
    }

    public void FetchConversations()
    {
        StartCoroutine(FetchConversationsCoroutine(_endpointVersion));
    }

    private IEnumerator CheckHealthCoroutine(int endpointVersion)
    {
        var url = $"{_restBaseUrl}/health";
        using (var req = UnityWebRequest.Get(url))
        {
            yield return req.SendWebRequest();

            if (endpointVersion != _endpointVersion)
                yield break;

            var message = req.result != UnityWebRequest.Result.Success
                ? $"[EllmoTools] Health check failed: {req.error}"
                : req.downloadHandler.text;

            _onLogMessage?.Invoke(ToToken(message));
        }
    }

    private IEnumerator FetchMachinesCoroutine(int endpointVersion)
    {
        var url = $"{_restBaseUrl}/hosts";
        using (var req = UnityWebRequest.Get(url))
        {
            yield return req.SendWebRequest();

            if (endpointVersion != _endpointVersion)
                yield break;

            var message = req.result != UnityWebRequest.Result.Success
                ? $"[EllmoTools] FetchHosts failed: {req.error}"
                : req.downloadHandler.text;

            _onLogMessage?.Invoke(ToToken(message));
        }
    }

    private IEnumerator FetchConversationsCoroutine(int endpointVersion)
    {
        var url = $"{_restBaseUrl}/conversations";
        using (var req = UnityWebRequest.Get(url))
        {
            yield return req.SendWebRequest();

            if (endpointVersion != _endpointVersion)
                yield break;

            var message = req.result != UnityWebRequest.Result.Success
                ? $"[EllmoTools] FetchConversations failed: {req.error}"
                : req.downloadHandler.text;

            _onLogMessage?.Invoke(ToToken(message));
        }
    }
}
