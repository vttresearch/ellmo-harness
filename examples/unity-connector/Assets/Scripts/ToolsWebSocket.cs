using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

public class ToolsWebSocket : MonoBehaviour
{
    private readonly SemaphoreSlim _lifecycleGate = new SemaphoreSlim(1, 1);
    private readonly SemaphoreSlim _sendGate = new SemaphoreSlim(1, 1);

    private string _wsBaseUrl;
    private string _conversationId;
    private Action<JToken> _onLogMessage;
    private Action<JToken> _onAssistantMessage;

    private ClientWebSocket _webSocket;
    private CancellationTokenSource _wsCts;
    private int _connectionVersion;
    private string _desiredHost;
    private string _desiredModel;
    private bool _destroying;

    [Serializable]
    private class WsConfigureMessage
    {
        public string type = "configure";
        public string host;
        public string model;
    }

    [Serializable]
    private class WsUserMessage
    {
        public string type = "user_message";
        public string text;
    }

    public bool IsConnected => _webSocket != null && _webSocket.State == WebSocketState.Open;

    public void Initialize(string wsBaseUrl, string conversationId, Action<JToken> onLogMessage, Action<JToken> onAssistantMessage)
    {
        _wsBaseUrl = wsBaseUrl;
        _conversationId = conversationId;
        _onLogMessage = onLogMessage;
        _onAssistantMessage = onAssistantMessage;
    }

    public async void ConnectWebSocket(string host = null, string model = null)
    {
        await ConnectWebSocketAsync(host, model);
    }

    public async Task<bool> ConnectWebSocketAsync(string host = null, string model = null)
    {
        if (!string.IsNullOrWhiteSpace(host) || !string.IsNullOrWhiteSpace(model))
            RememberConfiguration(host, model);

        var connected = await EnsureConnectedAsync();
        if (connected && (!string.IsNullOrWhiteSpace(host) || !string.IsNullOrWhiteSpace(model)))
            await SendRememberedConfigurationAsync();

        return connected;
    }

    /// <summary>
    /// Cancels any pending attempt and creates a fresh socket for a new endpoint.
    /// The old backend's host/model selection is intentionally cleared.
    /// </summary>
    public async Task<bool> ReconnectWebSocketAsync(string wsBaseUrl, string conversationId)
    {
        _wsBaseUrl = wsBaseUrl;
        _conversationId = conversationId;
        _desiredHost = null;
        _desiredModel = null;

        // Cancellation happens before taking the gate so a ConnectAsync currently
        // holding it cannot delay a server change until its network timeout.
        _wsCts?.Cancel();

        await _lifecycleGate.WaitAsync();
        try
        {
            await DisposeCurrentSocketLockedAsync(graceful: false, "server changed");
        }
        finally
        {
            _lifecycleGate.Release();
        }

        return await EnsureConnectedAsync();
    }

    public async Task SendConfigureAsync(string host, string model)
    {
        RememberConfiguration(host, model);

        // A hosts response can beat the initial WebSocket attempt. In that case,
        // selecting/configuring a host also retries the connection instead of
        // silently dropping the configuration message.
        if (!await EnsureConnectedAsync())
            return;

        await SendRememberedConfigurationAsync();
    }

    public async void SendUserMessage(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return;

        if (!await EnsureConnectedAsync())
        {
            Log("[EllmoTools] WebSocket is not connected; the message was not sent.");
            return;
        }

        var msgObj = new WsUserMessage { text = text };
        await SendJsonAsync(JsonUtility.ToJson(msgObj), "SendUserMessage");
    }

    /// <summary>
    /// Replies to a correlated Unity tool request on the current WebSocket.
    /// Tool results are deliberately not retried on a new connection because
    /// their request ids only belong to the connection that issued them.
    /// </summary>
    public Task SendUnityToolResultAsync(
        string toolName,
        string requestId,
        bool succeeded,
        JObject payload)
    {
        var data = new JObject
        {
            ["tool_name"] = toolName,
            ["request_id"] = requestId,
            ["ok"] = succeeded,
        };

        if (payload != null)
        {
            foreach (var property in payload.Properties())
                data[property.Name] = property.Value.DeepClone();
        }

        var message = new JObject
        {
            ["type"] = "unity_tool_result",
            ["data"] = data,
        };

        return SendJsonAsync(message.ToString(Formatting.None), "SendUnityToolResult");
    }

    private void RememberConfiguration(string host, string model)
    {
        _desiredHost = host;
        _desiredModel = model;
    }

    private async Task SendRememberedConfigurationAsync()
    {
        if (string.IsNullOrWhiteSpace(_desiredHost) && string.IsNullOrWhiteSpace(_desiredModel))
            return;

        var msg = new WsConfigureMessage
        {
            host = _desiredHost,
            model = _desiredModel,
        };

        await SendJsonAsync(JsonUtility.ToJson(msg), "SendConfigure");
    }

    private async Task<bool> EnsureConnectedAsync()
    {
        if (_destroying)
            return false;

        await _lifecycleGate.WaitAsync();
        try
        {
            if (_webSocket != null && _webSocket.State == WebSocketState.Open)
                return true;

            await DisposeCurrentSocketLockedAsync(graceful: false, "reconnecting");

            if (string.IsNullOrWhiteSpace(_wsBaseUrl) || string.IsNullOrWhiteSpace(_conversationId))
            {
                Log("[EllmoTools] WebSocket endpoint is not configured.");
                return false;
            }

            Uri uri;
            try
            {
                uri = new Uri($"{_wsBaseUrl.TrimEnd('/')}/{Uri.EscapeDataString(_conversationId)}");
            }
            catch (Exception ex)
            {
                Log($"[EllmoTools] Invalid WebSocket endpoint: {ex.Message}");
                return false;
            }

            var socket = new ClientWebSocket();
            var cts = new CancellationTokenSource();
            var version = ++_connectionVersion;
            _webSocket = socket;
            _wsCts = cts;

            try
            {
                using (var connectCts = CancellationTokenSource.CreateLinkedTokenSource(cts.Token))
                {
                    connectCts.CancelAfter(TimeSpan.FromSeconds(10));
                    await socket.ConnectAsync(uri, connectCts.Token);
                }
            }
            catch (OperationCanceledException)
            {
                if (!cts.IsCancellationRequested)
                    Log($"[EllmoTools] WebSocket connect timed out: {uri}.");
                await DisposeOwnedSocketLockedAsync(socket, cts, graceful: false, "connect cancelled");
                return false;
            }
            catch (Exception ex)
            {
                Log($"[EllmoTools] WebSocket connect failed: {ex.Message}");
                await DisposeOwnedSocketLockedAsync(socket, cts, graceful: false, "connect failed");
                return false;
            }

            if (!ReferenceEquals(_webSocket, socket) || cts.IsCancellationRequested)
            {
                await DisposeOwnedSocketLockedAsync(socket, cts, graceful: false, "superseded");
                return false;
            }

            Log($"[EllmoTools] WebSocket connected to {uri}.");
            _ = ReceiveLoop(socket, cts, version);
            return true;
        }
        finally
        {
            _lifecycleGate.Release();
        }
    }

    private async Task SendJsonAsync(string json, string operationName)
    {
        var socket = _webSocket;
        var cts = _wsCts;
        if (socket == null || cts == null || socket.State != WebSocketState.Open)
            return;

        var bytes = Encoding.UTF8.GetBytes(json);
        var buffer = new ArraySegment<byte>(bytes);
        Exception sendException = null;

        await _sendGate.WaitAsync();
        try
        {
            if (!ReferenceEquals(socket, _webSocket) || socket.State != WebSocketState.Open)
                return;

            await socket.SendAsync(buffer, WebSocketMessageType.Text, true, cts.Token);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            sendException = ex;
            Log($"[EllmoTools] {operationName} failed: {ex.Message}");
        }
        finally
        {
            _sendGate.Release();
        }

        if (sendException != null)
            await InvalidateSocketAsync(socket, cts);
    }

    private async Task ReceiveLoop(ClientWebSocket socket, CancellationTokenSource cts, int version)
    {
        var buffer = new ArraySegment<byte>(new byte[8192]);
        var sb = new StringBuilder();

        try
        {
            while (!cts.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                sb.Clear();
                WebSocketReceiveResult result;

                do
                {
                    result = await socket.ReceiveAsync(buffer, cts.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                        return;

                    var chunk = Encoding.UTF8.GetString(buffer.Array, 0, result.Count);
                    sb.Append(chunk);
                }
                while (!result.EndOfMessage);

                var jsonText = sb.ToString();
                try
                {
                    HandleServerEvent(JToken.Parse(jsonText));
                }
                catch
                {
                    EmitLog(JValue.CreateString(jsonText));
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            Log($"[EllmoTools] ReceiveLoop error: {ex.Message}");
        }
        finally
        {
            await InvalidateSocketAsync(socket, cts, version);
        }
    }

    private async Task InvalidateSocketAsync(ClientWebSocket socket, CancellationTokenSource cts, int version = -1)
    {
        await _lifecycleGate.WaitAsync();
        try
        {
            if (!ReferenceEquals(_webSocket, socket) || !ReferenceEquals(_wsCts, cts))
                return;
            if (version >= 0 && version != _connectionVersion)
                return;

            await DisposeOwnedSocketLockedAsync(socket, cts, graceful: false, "connection ended");
        }
        finally
        {
            _lifecycleGate.Release();
        }
    }

    private void HandleServerEvent(JToken token)
    {
        var tokenObject = token as JObject;
        var type = tokenObject?["type"]?.Value<string>();

        if (string.IsNullOrEmpty(type))
        {
            EmitLog(token ?? JValue.CreateNull());
            return;
        }

        switch (type)
        {
            case "log":
            case "error":
                EmitLog(token);
                break;

            case "assistant_message":
                EmitAssistantMessage(token);
                break;

            case "agent_message":
                var kind = tokenObject["data"]?["kind"]?.Value<string>();
                if (kind == "assistant_message")
                {
                    EmitAssistantMessage(token);
                }
                else
                {
                    EmitLog(token);

                    // A model can attach a short spoken lead-in to the same
                    // response that contains its tool call. Keep the tool details
                    // in the log while also forwarding that text to chat and TTS.
                    var content = tokenObject["data"]?["content"]?.Value<string>();
                    if (kind == "tool_call" && !string.IsNullOrWhiteSpace(content))
                        EmitAssistantMessage(token);
                }
                break;

            default:
                EmitLog(token);
                break;
        }
    }

    public async void CloseWebSocket()
    {
        await CloseWebSocketAsync();
    }

    public async Task CloseWebSocketAsync()
    {
        _wsCts?.Cancel();

        await _lifecycleGate.WaitAsync();
        try
        {
            await DisposeCurrentSocketLockedAsync(graceful: true, "client closed");
        }
        finally
        {
            _lifecycleGate.Release();
        }
    }

    private Task DisposeCurrentSocketLockedAsync(bool graceful, string reason)
    {
        return DisposeOwnedSocketLockedAsync(_webSocket, _wsCts, graceful, reason);
    }

    private async Task DisposeOwnedSocketLockedAsync(
        ClientWebSocket socket,
        CancellationTokenSource cts,
        bool graceful,
        string reason)
    {
        if (socket == null && cts == null)
            return;

        cts?.Cancel();

        try
        {
            if (graceful && socket != null &&
                (socket.State == WebSocketState.Open || socket.State == WebSocketState.CloseReceived))
            {
                using (var closeCts = new CancellationTokenSource(TimeSpan.FromSeconds(2)))
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, reason, closeCts.Token);
            }
            else
            {
                socket?.Abort();
            }
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[EllmoTools] WebSocket close warning: {ex.Message}");
            socket?.Abort();
        }
        finally
        {
            socket?.Dispose();
            cts?.Dispose();

            if (ReferenceEquals(_webSocket, socket))
                _webSocket = null;
            if (ReferenceEquals(_wsCts, cts))
                _wsCts = null;
        }
    }

    private void Log(string message)
    {
        EmitLog(JValue.CreateString(message));
    }

    private void EmitLog(JToken token)
    {
        try
        {
            _onLogMessage?.Invoke(token);
        }
        catch (Exception ex)
        {
            Debug.LogException(ex, this);
        }
    }

    private void EmitAssistantMessage(JToken token)
    {
        try
        {
            _onAssistantMessage?.Invoke(token);
        }
        catch (Exception ex)
        {
            Debug.LogException(ex, this);
        }
    }

    private void OnDestroy()
    {
        _destroying = true;
        _wsCts?.Cancel();
        _webSocket?.Abort();
    }
}
