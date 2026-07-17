using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace BuddyShell.Bridge;

public sealed class BridgeClient : IDisposable
{
    private readonly HttpClient _http = new(new HttpClientHandler { UseProxy = false });
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
    private ShellSettings _settings;

    public BridgeClient(ShellSettings settings) => _settings = settings;

    public void UpdateSettings(ShellSettings settings) => _settings = settings;

    public Task<VPetStateResponse> GetStateAsync(CancellationToken cancellationToken = default) =>
        SendRequiredAsync<VPetStateResponse>(HttpMethod.Get, "/api/vpet/state", null, 5, cancellationToken);

    public Task<BodyStepResponse> StepBodyAsync(
        BodyStepRequest request,
        CancellationToken cancellationToken = default) =>
        SendRequiredAsync<BodyStepResponse>(
            HttpMethod.Post,
            "/api/body/step",
            request,
            15,
            cancellationToken);

    public Task<VPetEventResponse> SendEventAsync(
        VPetEventRequest request,
        CancellationToken cancellationToken = default) =>
        SendRequiredAsync<VPetEventResponse>(
            HttpMethod.Post,
            "/api/vpet/event",
            request,
            request.Event == "presence_heartbeat" ? 5 : 15,
            cancellationToken);

    public Task<VPetPendingResponse> DrainAsync(
        bool digest,
        CancellationToken cancellationToken = default) =>
        SendRequiredAsync<VPetPendingResponse>(
            HttpMethod.Post,
            "/api/vpet/pending/drain",
            new VPetDrainRequest { Digest = digest },
            5,
            cancellationToken);

    public void Dispose() => _http.Dispose();

    private async Task<T> SendRequiredAsync<T>(
        HttpMethod method,
        string path,
        object? body,
        int timeoutSeconds,
        CancellationToken cancellationToken)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        linked.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));
        using var request = new HttpRequestMessage(method, BuildUri(path));
        AddHeaders(request);
        if (body is not null)
        {
            request.Content = new StringContent(
                JsonSerializer.Serialize(body, _json),
                Encoding.UTF8,
                "application/json");
        }

        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, linked.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            throw new BridgeRequestException("MyBuddy bridge request timed out.", null, exception);
        }
        catch (HttpRequestException exception)
        {
            throw new BridgeRequestException("MyBuddy bridge is unreachable.", null, exception);
        }

        using (response)
        {
            var text = await response.Content.ReadAsStringAsync(linked.Token).ConfigureAwait(false);
            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                throw new BridgeRequestException("MyBuddy token rejected.", 401);
            }
            if (!response.IsSuccessStatusCode)
            {
                throw new BridgeRequestException(
                    $"MyBuddy bridge returned {(int)response.StatusCode}: {text}",
                    (int)response.StatusCode);
            }
            var value = JsonSerializer.Deserialize<T>(text, _json);
            return value ?? throw new BridgeRequestException("MyBuddy bridge returned an empty response.");
        }
    }

    private Uri BuildUri(string path)
    {
        var root = (_settings.BridgeUrl ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(root)) root = "http://127.0.0.1:8000";
        return new Uri(root + path, UriKind.Absolute);
    }

    private void AddHeaders(HttpRequestMessage request)
    {
        if (!string.IsNullOrWhiteSpace(_settings.BridgeToken))
        {
            request.Headers.TryAddWithoutValidation("X-MyBuddy-Token", _settings.BridgeToken.Trim());
        }
        request.Headers.TryAddWithoutValidation(
            "X-MyBuddy-Client-Flags",
            JsonSerializer.Serialize(_settings.ToClientFlags(), _json));
    }
}
