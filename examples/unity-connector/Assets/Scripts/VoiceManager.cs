using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

public class VoiceManager : MonoBehaviour
{
    public enum InteractionState
    {
        Idle,
        RequestingPermission,
        Recording,
        Transcribing,
        Error,
    }

    [Header("Voice backend")]
    [SerializeField] private string voiceServerAddress = "http://127.0.0.1:5050";
    [SerializeField] private bool followEllmoServerHost = true;
    [SerializeField, Range(1, 65535)] private int voiceServerPort = 5050;

    [Header("Speech capture")]
    [SerializeField, Range(1, 30)] private int maxRecordingTime = 10;

    [Header("Speech playback")]
    [Tooltip("Long responses are synthesized in sentence-sized pieces. The next piece is generated while the current one plays.")]
    [SerializeField, Range(40, 500)] private int ttsChunkCharacterLimit = 180;
    [SerializeField] private int ttsSampleRate = 24000;

    private const int SttSampleRate = 16000;
    private const int VoiceRequestTimeoutSeconds = 120;

    private AudioClip _currentRecordingClip;
    private string _activeMicrophoneDevice;
    private Coroutine _recordingTimeoutCoroutine;
    private bool _requestingMicrophonePermission;
    private bool _finishingRecording;
    private UnityWebRequest _activeSttRequest;

    private readonly Queue<PendingAudioChunk> _audioQueue = new Queue<PendingAudioChunk>();
    private AudioSource _audioSource;
    private Coroutine _speechFetchCoroutine;
    private Coroutine _playbackCoroutine;
    private UnityWebRequest _activeTtsRequest;
    private int _speechGeneration;
    private float _speechRequestStartedAt;
    private bool _firstAudioStarted;
    private AudioSource _toolCallCueAudioSource;
    private GameObject _toolCallCueAudioObject;
    private AudioClip _toolCallCueClip;

    public event Action<string> TranscriptReady;
    public event Action<InteractionState, string> StateChanged;

    public InteractionState State { get; private set; } = InteractionState.Idle;
    public bool IsRecording => State == InteractionState.Recording;
    public string VoiceServerAddress => voiceServerAddress;

    private sealed class PendingAudioChunk
    {
        public bool IsReady;
        public AudioClip Clip;
    }

    /// <summary>
    /// Uses the chat server's host for speech as well, while keeping the dedicated
    /// voice port. This lets the same runtime settings work on desktop and XR.
    /// </summary>
    public void ConfigureServerHost(string address)
    {
        if (!followEllmoServerHost || string.IsNullOrWhiteSpace(address))
            return;

        var candidate = address.Contains("://", StringComparison.Ordinal)
            ? address
            : $"http://{address}";

        if (!Uri.TryCreate(candidate, UriKind.Absolute, out var uri))
        {
            Debug.LogWarning($"[Voice Manager] Cannot derive the voice server from '{address}'.");
            return;
        }

        var scheme = string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)
            ? Uri.UriSchemeHttps
            : Uri.UriSchemeHttp;
        var builder = new UriBuilder(scheme, uri.Host, voiceServerPort);
        voiceServerAddress = builder.Uri.GetLeftPart(UriPartial.Authority).TrimEnd('/');
        Debug.Log($"[Voice Manager] Voice server: {voiceServerAddress}");
    }

    public void ToggleRecording()
    {
        switch (State)
        {
            case InteractionState.Recording:
                _ = FinishRecordingAndNotifyAsync();
                break;
            case InteractionState.Idle:
            case InteractionState.Error:
                StartRecording();
                break;
        }
    }

    public void StartRecording()
    {
        if (_requestingMicrophonePermission || _finishingRecording || IsRecording)
            return;

        StopToolCallCue();
        StopSpeaking();
        StartCoroutine(StartRecordingWithPermission());
    }

    private IEnumerator StartRecordingWithPermission()
    {
        _requestingMicrophonePermission = true;

        if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
        {
            SetState(InteractionState.RequestingPermission, "Allow microphone...");
            yield return Application.RequestUserAuthorization(UserAuthorization.Microphone);
        }

        _requestingMicrophonePermission = false;
        if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
        {
            SetState(InteractionState.Error, "Microphone denied");
            Debug.LogWarning("[Voice Manager] Microphone permission was not granted.");
            yield break;
        }

        if (Microphone.devices.Length == 0)
        {
            SetState(InteractionState.Error, "No microphone");
            Debug.LogWarning("[Voice Manager] No microphone is available.");
            yield break;
        }

        _activeMicrophoneDevice = Microphone.devices[0];
        _currentRecordingClip = Microphone.Start(
            _activeMicrophoneDevice,
            false,
            maxRecordingTime,
            SttSampleRate);

        if (_currentRecordingClip == null)
        {
            _activeMicrophoneDevice = null;
            SetState(InteractionState.Error, "Microphone failed");
            Debug.LogWarning("[Voice Manager] Failed to start recording.");
            yield break;
        }

        SetState(InteractionState.Recording, "Stop & send");
        _recordingTimeoutCoroutine = StartCoroutine(AutoStopRecordingAfterTimeout(maxRecordingTime));
    }

    private async Task FinishRecordingAndNotifyAsync()
    {
        if (_finishingRecording || _currentRecordingClip == null)
            return;

        _finishingRecording = true;
        SetState(InteractionState.Transcribing, "Transcribing...");

        string transcript;
        try
        {
            transcript = await StopRecordingAndSendAsync();
        }
        catch (Exception exception)
        {
            Debug.LogException(exception, this);
            transcript = null;
        }
        finally
        {
            _finishingRecording = false;
        }

        if (this == null)
            return;

        if (string.IsNullOrWhiteSpace(transcript))
        {
            SetState(InteractionState.Error, "Try again");
            return;
        }

        SetState(InteractionState.Idle, "Talk");
        TranscriptReady?.Invoke(transcript.Trim());
    }

    public async Task<string> StopRecordingAndSendAsync()
    {
        if (_recordingTimeoutCoroutine != null)
        {
            StopCoroutine(_recordingTimeoutCoroutine);
            _recordingTimeoutCoroutine = null;
        }

        if (_currentRecordingClip == null)
        {
            Debug.LogWarning("[Voice Manager] No recording is in progress.");
            return null;
        }

        var isRecording = Microphone.IsRecording(_activeMicrophoneDevice);
        if (!isRecording && _currentRecordingClip.loadState != AudioDataLoadState.Loaded)
        {
            Debug.LogWarning("[Voice Manager] The microphone did not activate properly.");
            ClearRecording();
            return null;
        }

        var recordedFrames = isRecording
            ? Microphone.GetPosition(_activeMicrophoneDevice)
            : _currentRecordingClip.samples;
        Microphone.End(_activeMicrophoneDevice);

        if (recordedFrames <= 0)
        {
            Debug.LogWarning("[Voice Manager] The microphone did not capture any audio.");
            ClearRecording();
            return null;
        }

        var samples = ReadMonoSamples(_currentRecordingClip, recordedFrames);
        ClearRecording();
        if (samples.Length == 0)
            return null;

        var wavBytes = ConvertToWav(samples, SttSampleRate);
        var form = new WWWForm();
        form.AddBinaryData("audio", wavBytes, "recording.wav", "audio/wav");

        var www = UnityWebRequest.Post($"{voiceServerAddress.TrimEnd('/')}/generate-text", form);
        www.downloadHandler = new DownloadHandlerBuffer();
        www.timeout = VoiceRequestTimeoutSeconds;
        _activeSttRequest = www;

        try
        {
            var operation = www.SendWebRequest();
            while (!operation.isDone)
                await Task.Yield();

            if (www.result == UnityWebRequest.Result.Success)
                return www.downloadHandler.text;

            Debug.LogError($"[Voice Manager] Transcription failed: {www.error}");
            return null;
        }
        finally
        {
            if (ReferenceEquals(_activeSttRequest, www))
                _activeSttRequest = null;
            www.Dispose();
        }
    }

    private IEnumerator AutoStopRecordingAfterTimeout(int duration)
    {
        yield return new WaitForSeconds(duration);
        _recordingTimeoutCoroutine = null;
        Debug.Log("[Voice Manager] Maximum recording duration reached; transcribing.");
        _ = FinishRecordingAndNotifyAsync();
    }

    /// <summary>
    /// Starts a repeating nonverbal cue when the assistant begins a tool call
    /// without providing its own spoken lead-in.
    /// </summary>
    public void StartToolCallCue()
    {
        if (_toolCallCueAudioSource == null)
        {
            _toolCallCueAudioObject = new GameObject("Tool Call Cue Audio", typeof(AudioSource));
            _toolCallCueAudioObject.transform.SetParent(transform, false);
            _toolCallCueAudioSource = _toolCallCueAudioObject.GetComponent<AudioSource>();
            _toolCallCueAudioSource.playOnAwake = false;
            _toolCallCueAudioSource.loop = true;
            _toolCallCueAudioSource.spatialBlend = 0f;
            _toolCallCueAudioSource.volume = 0.48f;
        }

        _toolCallCueClip ??= CreateToolCallCueClip();
        if (_toolCallCueAudioSource.isPlaying)
            return;

        _toolCallCueAudioSource.clip = _toolCallCueClip;
        _toolCallCueAudioSource.Play();
    }

    public void StopToolCallCue()
    {
        if (_toolCallCueAudioSource == null)
            return;

        _toolCallCueAudioSource.Stop();
        _toolCallCueAudioSource.clip = null;
    }

    /// <summary>
    /// Speaks a complete assistant message. The message is split into short chunks
    /// so the first audio starts quickly, while later chunks generate during playback.
    /// </summary>
    public void Speak(string text)
    {
        StopSpeaking();

        if (State == InteractionState.RequestingPermission ||
            State == InteractionState.Recording ||
            State == InteractionState.Transcribing)
        {
            Debug.Log("[Voice Manager] Skipped assistant speech because a newer voice interaction is in progress.");
            return;
        }

        var chunks = SplitForSpeech(text, ttsChunkCharacterLimit);
        if (chunks.Count == 0)
            return;

        var generation = _speechGeneration;
        _speechRequestStartedAt = Time.realtimeSinceStartup;
        _firstAudioStarted = false;
        _speechFetchCoroutine = StartCoroutine(FetchSpeechChunks(chunks, generation));
    }

    /// <summary>
    /// Backwards-compatible coroutine entry point used by older scene bindings.
    /// </summary>
    public IEnumerator FetchAndPlayAudio(string textToSend)
    {
        Speak(textToSend);
        yield break;
    }

    public void StopSpeaking()
    {
        _speechGeneration++;

        if (_activeTtsRequest != null)
        {
            _activeTtsRequest.Abort();
            _activeTtsRequest = null;
        }

        if (_speechFetchCoroutine != null)
        {
            StopCoroutine(_speechFetchCoroutine);
            _speechFetchCoroutine = null;
        }

        if (_playbackCoroutine != null)
        {
            StopCoroutine(_playbackCoroutine);
            _playbackCoroutine = null;
        }

        if (_audioSource != null)
        {
            _audioSource.Stop();
            if (_audioSource.clip != null)
            {
                Destroy(_audioSource.clip);
                _audioSource.clip = null;
            }
        }

        while (_audioQueue.Count > 0)
        {
            var pending = _audioQueue.Dequeue();
            if (pending.Clip != null)
                Destroy(pending.Clip);
        }
    }

    private IEnumerator FetchSpeechChunks(IReadOnlyList<string> chunks, int generation)
    {
        foreach (var chunkText in chunks)
        {
            if (generation != _speechGeneration)
                yield break;

            var pending = new PendingAudioChunk();
            _audioQueue.Enqueue(pending);

            if (_playbackCoroutine == null)
                _playbackCoroutine = StartCoroutine(PlayAudioQueue(generation));

            yield return FetchAudioChunk(chunkText, pending, generation);
        }

        if (generation == _speechGeneration)
            _speechFetchCoroutine = null;
    }

    private IEnumerator FetchAudioChunk(string textToSend, PendingAudioChunk pending, int generation)
    {
        var jsonData = JsonUtility.ToJson(new TextRequest { text = textToSend });
        using (var www = new UnityWebRequest(
                   $"{voiceServerAddress.TrimEnd('/')}/generate-audio",
                   UnityWebRequest.kHttpVerbPOST))
        {
            www.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(jsonData));
            www.downloadHandler = new DownloadHandlerBuffer();
            www.SetRequestHeader("Content-Type", "application/json");
            www.timeout = VoiceRequestTimeoutSeconds;
            _activeTtsRequest = www;

            yield return www.SendWebRequest();

            if (ReferenceEquals(_activeTtsRequest, www))
                _activeTtsRequest = null;

            if (generation != _speechGeneration)
                yield break;

            if (www.result != UnityWebRequest.Result.Success)
            {
                Debug.LogError($"[Voice Manager] Audio fetch failed: {www.error}");
                pending.IsReady = true;
                yield break;
            }

            var data = www.downloadHandler.data;
            if (data == null || data.Length == 0 || data.Length % sizeof(float) != 0)
            {
                Debug.LogError("[Voice Manager] The TTS backend returned invalid float audio.");
                pending.IsReady = true;
                yield break;
            }

            var audioSamples = new float[data.Length / sizeof(float)];
            Buffer.BlockCopy(data, 0, audioSamples, 0, data.Length);

            var clip = AudioClip.Create(
                "Kokoro speech chunk",
                audioSamples.Length,
                1,
                ttsSampleRate,
                false);
            clip.SetData(audioSamples, 0);
            pending.Clip = clip;
            pending.IsReady = true;
        }
    }

    private IEnumerator PlayAudioQueue(int generation)
    {
        EnsureAudioSource();

        while (generation == _speechGeneration)
        {
            if (_audioQueue.Count == 0)
            {
                if (_speechFetchCoroutine == null)
                    break;
                yield return null;
                continue;
            }

            var pending = _audioQueue.Peek();
            yield return new WaitUntil(() => pending.IsReady || generation != _speechGeneration);
            if (generation != _speechGeneration)
                break;

            _audioQueue.Dequeue();
            if (pending.Clip == null)
                continue;

            if (!_firstAudioStarted)
            {
                _firstAudioStarted = true;
                var timeToFirstAudio = Time.realtimeSinceStartup - _speechRequestStartedAt;
                Debug.Log($"[Voice Manager] Kokoro time to first audio: {timeToFirstAudio:F2} s");
            }

            StopToolCallCue();
            _audioSource.clip = pending.Clip;
            _audioSource.Play();
            yield return new WaitWhile(() => _audioSource.isPlaying && generation == _speechGeneration);

            if (_audioSource.clip == pending.Clip)
                _audioSource.clip = null;
            Destroy(pending.Clip);
        }

        if (generation == _speechGeneration)
        {
            _playbackCoroutine = null;
            StopToolCallCue();
        }
    }

    private void EnsureAudioSource()
    {
        if (_audioSource == null)
            _audioSource = GetComponent<AudioSource>();
        if (_audioSource == null)
            _audioSource = gameObject.AddComponent<AudioSource>();

        _audioSource.playOnAwake = false;
        _audioSource.spatialBlend = 0f;
    }

    private static AudioClip CreateToolCallCueClip()
    {
        const int sampleRate = 44100;
        const float duration = 1.28f;
        const float tapDuration = 0.09f;
        const float attackDuration = 0.004f;
        float[] tapStarts = { 0.08f, 0.28f, 0.48f };
        float[] tapFrequencies = { 240f, 240f, 310f };
        int sampleCount = Mathf.CeilToInt(sampleRate * duration);
        var samples = new float[sampleCount];
        uint noiseState = 0x7f4a7c15u;

        for (int i = 0; i < sampleCount; ++i)
        {
            float time = i / (float)sampleRate;
            float sample = 0f;

            noiseState = noiseState * 1664525u + 1013904223u;
            float noise = ((noiseState >> 8) / 8388607.5f) - 1f;

            for (int tapIndex = 0; tapIndex < tapStarts.Length; ++tapIndex)
            {
                float tapTime = time - tapStarts[tapIndex];
                if (tapTime < 0f || tapTime > tapDuration)
                    continue;

                float attack = Mathf.Clamp01(tapTime / attackDuration);
                float decay = Mathf.Exp(-tapTime * 38f);
                float envelope = attack * decay;
                float tapFrequency = tapFrequencies[tapIndex];
                float body = Mathf.Sin(tapTime * 2f * Mathf.PI * tapFrequency) * 0.7f;
                float woodenOvertone = Mathf.Sin(tapTime * 2f * Mathf.PI * tapFrequency * 1.72f) * 0.22f;
                float transient = noise * Mathf.Exp(-tapTime * 95f) * 0.12f;
                sample += (body + woodenOvertone + transient) * envelope * 0.42f;
            }

            samples[i] = sample;
        }

        var clip = AudioClip.Create("Generated Tool Call Cue", sampleCount, 1, sampleRate, false);
        clip.SetData(samples, 0);
        return clip;
    }

    private void SetState(InteractionState state, string status)
    {
        State = state;
        StateChanged?.Invoke(state, status);
    }

    private void ClearRecording()
    {
        _currentRecordingClip = null;
        _activeMicrophoneDevice = null;
    }

    private void OnDestroy()
    {
        if (!string.IsNullOrEmpty(_activeMicrophoneDevice))
            Microphone.End(_activeMicrophoneDevice);

        _activeSttRequest?.Abort();
        StopSpeaking();
        StopToolCallCue();

        if (_toolCallCueClip != null)
            Destroy(_toolCallCueClip);
        if (_toolCallCueAudioObject != null)
            Destroy(_toolCallCueAudioObject);
    }

    private static float[] ReadMonoSamples(AudioClip clip, int frameCount)
    {
        frameCount = Mathf.Min(frameCount, clip.samples);
        var interleavedSamples = new float[frameCount * clip.channels];
        if (!clip.GetData(interleavedSamples, 0))
        {
            Debug.LogWarning("[Voice Manager] Failed to read the recorded audio.");
            return Array.Empty<float>();
        }

        if (clip.channels == 1)
            return interleavedSamples;

        var monoSamples = new float[frameCount];
        for (var frame = 0; frame < frameCount; frame++)
        {
            var sum = 0f;
            for (var channel = 0; channel < clip.channels; channel++)
                sum += interleavedSamples[frame * clip.channels + channel];
            monoSamples[frame] = sum / clip.channels;
        }

        return monoSamples;
    }

    private static byte[] ConvertToWav(float[] samples, int sampleRate)
    {
        var bytes = new List<byte>(44 + samples.Length * sizeof(short));
        var dataSize = samples.Length * sizeof(short);
        var byteRate = sampleRate * sizeof(short);

        bytes.AddRange(Encoding.ASCII.GetBytes("RIFF"));
        bytes.AddRange(BitConverter.GetBytes(36 + dataSize));
        bytes.AddRange(Encoding.ASCII.GetBytes("WAVE"));

        bytes.AddRange(Encoding.ASCII.GetBytes("fmt "));
        bytes.AddRange(BitConverter.GetBytes(16));
        bytes.AddRange(BitConverter.GetBytes((short)1));
        bytes.AddRange(BitConverter.GetBytes((short)1));
        bytes.AddRange(BitConverter.GetBytes(sampleRate));
        bytes.AddRange(BitConverter.GetBytes(byteRate));
        bytes.AddRange(BitConverter.GetBytes((short)sizeof(short)));
        bytes.AddRange(BitConverter.GetBytes((short)16));

        bytes.AddRange(Encoding.ASCII.GetBytes("data"));
        bytes.AddRange(BitConverter.GetBytes(dataSize));

        foreach (var sample in samples)
        {
            var pcmSample = (short)(Mathf.Clamp(sample, -1f, 1f) * short.MaxValue);
            bytes.AddRange(BitConverter.GetBytes(pcmSample));
        }

        return bytes.ToArray();
    }

    private static List<string> SplitForSpeech(string text, int maxCharacters)
    {
        var chunks = new List<string>();
        if (string.IsNullOrWhiteSpace(text))
            return chunks;

        maxCharacters = Mathf.Max(40, maxCharacters);
        var normalized = Regex.Replace(text.Trim(), @"\s+", " ");
        var sentences = Regex.Split(normalized, @"(?<=[.!?;:])\s+");
        var current = new StringBuilder();

        foreach (var sentence in sentences)
        {
            if (string.IsNullOrWhiteSpace(sentence))
                continue;

            if (sentence.Length > maxCharacters)
            {
                FlushChunk(current, chunks);
                SplitLongSentence(sentence, maxCharacters, chunks);
                continue;
            }

            var extraLength = current.Length == 0 ? sentence.Length : sentence.Length + 1;
            if (current.Length > 0 && current.Length + extraLength > maxCharacters)
                FlushChunk(current, chunks);

            if (current.Length > 0)
                current.Append(' ');
            current.Append(sentence.Trim());
        }

        FlushChunk(current, chunks);
        return chunks;
    }

    private static void SplitLongSentence(string sentence, int maxCharacters, ICollection<string> chunks)
    {
        var current = new StringBuilder();
        foreach (var word in sentence.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var extraLength = current.Length == 0 ? word.Length : word.Length + 1;
            if (current.Length > 0 && current.Length + extraLength > maxCharacters)
                FlushChunk(current, chunks);

            if (current.Length > 0)
                current.Append(' ');
            current.Append(word);
        }

        FlushChunk(current, chunks);
    }

    private static void FlushChunk(StringBuilder chunk, ICollection<string> chunks)
    {
        if (chunk.Length == 0)
            return;

        chunks.Add(chunk.ToString());
        chunk.Clear();
    }

    [Serializable]
    private struct TextRequest
    {
        public string text;
    }
}
