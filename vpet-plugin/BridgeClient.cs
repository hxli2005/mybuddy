using System;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public sealed class BridgeClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _json;
    private BridgeSettings _settings;

    public BridgeClient(BridgeSettings settings)
    {
        _settings = settings;
        _http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(8),
        };
        _json = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
        };
    }

    public void UpdateSettings(BridgeSettings settings)
    {
        _settings = settings;
    }

    public Task<VPetStatusResponse?> GetStatusAsync(CancellationToken cancellationToken = default)
    {
        return SendAsync<VPetStatusResponse>(HttpMethod.Get, "/api/vpet/status", null, cancellationToken);
    }

    public Task<VPetBridgeResponse> SendChatAsync(
        string message,
        string eventName,
        BodyState? bodyState,
        CancellationToken cancellationToken = default)
    {
        var request = new VPetChatRequest
        {
            Message = message,
            Event = eventName,
            BodyState = bodyState,
        };
        return SendRequiredAsync<VPetBridgeResponse>(
            HttpMethod.Post,
            "/api/vpet/chat",
            request,
            cancellationToken);
    }

    public Task<VPetEventResponse> SendEventAsync(
        VPetEventRequest request,
        CancellationToken cancellationToken = default)
    {
        return SendRequiredAsync<VPetEventResponse>(
            HttpMethod.Post,
            "/api/vpet/event",
            request,
            cancellationToken);
    }

    public Task<VPetPendingResponse> PeekPendingAsync(CancellationToken cancellationToken = default)
    {
        return SendRequiredAsync<VPetPendingResponse>(
            HttpMethod.Get,
            "/api/vpet/pending",
            null,
            cancellationToken);
    }

    public Task<VPetPendingResponse> DrainDigestAsync(CancellationToken cancellationToken = default)
    {
        return DrainAsync(digest: true, cancellationToken);
    }

    public Task<VPetPendingResponse> DrainAsync(
        bool digest,
        CancellationToken cancellationToken = default)
    {
        return SendRequiredAsync<VPetPendingResponse>(
            HttpMethod.Post,
            "/api/vpet/pending/drain",
            new VPetDrainRequest { Digest = digest },
            cancellationToken);
    }

    public void Dispose()
    {
        _http.Dispose();
    }

    private async Task<T> SendRequiredAsync<T>(
        HttpMethod method,
        string path,
        object? body,
        CancellationToken cancellationToken)
    {
        var result = await SendAsync<T>(method, path, body, cancellationToken).ConfigureAwait(false);
        if (result == null)
        {
            throw new BridgeRequestException("MyBuddy bridge returned an empty response.");
        }
        return result;
    }

    private async Task<T?> SendAsync<T>(
        HttpMethod method,
        string path,
        object? body,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, BuildUri(path));
        AddHeaders(request);
        if (body != null)
        {
            var json = JsonSerializer.Serialize(body, _json);
            request.Content = new StringContent(json, Encoding.UTF8, "application/json");
        }

        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        }
        catch (TaskCanceledException e) when (!cancellationToken.IsCancellationRequested)
        {
            throw new BridgeRequestException("MyBuddy bridge request timed out.", null, e);
        }
        catch (HttpRequestException e)
        {
            throw new BridgeRequestException("MyBuddy bridge is unreachable.", null, e);
        }

        using (response)
        {
            var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                throw new BridgeRequestException("MyBuddy token rejected.", (int)response.StatusCode);
            }
            if (!response.IsSuccessStatusCode)
            {
                throw new BridgeRequestException(
                    $"MyBuddy bridge returned {(int)response.StatusCode}: {text}",
                    (int)response.StatusCode);
            }
            if (string.IsNullOrWhiteSpace(text))
            {
                return default;
            }
            return JsonSerializer.Deserialize<T>(text, _json);
        }
    }

    private Uri BuildUri(string path)
    {
        var root = (_settings.BridgeUrl ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(root))
        {
            root = "http://127.0.0.1:8000";
        }
        return new Uri(root + path, UriKind.Absolute);
    }

    private void AddHeaders(HttpRequestMessage request)
    {
        if (!string.IsNullOrWhiteSpace(_settings.BridgeToken))
        {
            request.Headers.TryAddWithoutValidation("X-MyBuddy-Token", _settings.BridgeToken.Trim());
        }

        var flags = JsonSerializer.Serialize(_settings.ToClientFlags(), _json);
        request.Headers.TryAddWithoutValidation("X-MyBuddy-Client-Flags", flags);
    }
}
