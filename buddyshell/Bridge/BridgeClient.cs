using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace BuddyShell.Bridge;

public sealed class BridgeClient(ShellSettings settings) : IDisposable
{
    private readonly HttpClient _http = new(new HttpClientHandler { UseProxy = false });
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
    private ShellSettings _settings = settings;

    public void UpdateSettings(ShellSettings value) => _settings = value;

    public async Task<BodyStepResponse> StepBodyAsync(
        BodyStepRequest body,
        CancellationToken cancellationToken = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        using var request = new HttpRequestMessage(HttpMethod.Post, BuildUri());
        request.Content = new StringContent(
            JsonSerializer.Serialize(body, _json),
            Encoding.UTF8,
            "application/json");
        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, timeout.Token).ConfigureAwait(false);
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
            var text = await response.Content.ReadAsStringAsync(timeout.Token).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
                throw new BridgeRequestException($"MyBuddy bridge returned {(int)response.StatusCode}: {text}", (int)response.StatusCode);
            return JsonSerializer.Deserialize<BodyStepResponse>(text, _json)
                ?? throw new BridgeRequestException("MyBuddy bridge returned an empty response.");
        }
    }

    private Uri BuildUri()
    {
        var root = (_settings.BridgeUrl ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(root)) root = "http://127.0.0.1:8000";
        return new Uri(root + "/api/body/step", UriKind.Absolute);
    }

    public void Dispose() => _http.Dispose();
}
