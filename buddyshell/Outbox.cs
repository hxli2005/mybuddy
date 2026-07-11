using BuddyShell.Bridge;
using System.IO;
using System.Text.Json;

namespace BuddyShell;

public sealed class OutboxItem
{
    public required VPetEventRequest Payload { get; init; }
    public DateTimeOffset CreatedAt { get; init; } = DateTimeOffset.UtcNow;
    public int Attempts { get; set; }
    public DateTimeOffset? NextAttemptAt { get; set; }
}

public sealed class Outbox
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };
    private readonly SemaphoreSlim _gate = new(1, 1);

    public static string PathName => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "BuddyShell",
        "outbox.jsonl");

    public async Task EnqueueAsync(VPetEventRequest payload)
    {
        await _gate.WaitAsync();
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(PathName)!);
            await File.AppendAllTextAsync(
                PathName,
                JsonSerializer.Serialize(new OutboxItem { Payload = payload }, Json) + Environment.NewLine);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task FlushAsync(BridgeClient client, CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (!File.Exists(PathName)) return;
            var now = DateTimeOffset.UtcNow;
            var remaining = new List<OutboxItem>();
            foreach (var line in await File.ReadAllLinesAsync(PathName, cancellationToken))
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                OutboxItem? item;
                try
                {
                    item = JsonSerializer.Deserialize<OutboxItem>(line, Json);
                }
                catch (JsonException exception)
                {
                    App.LogException(exception);
                    continue;
                }
                if (item is null) continue;
                if (now - item.CreatedAt > TimeSpan.FromHours(24))
                {
                    App.LogMessage($"outbox_dead_letter client_event_id={item.Payload.ClientEventId}");
                    continue;
                }
                if (item.NextAttemptAt is not null && item.NextAttemptAt > now)
                {
                    remaining.Add(item);
                    continue;
                }
                try
                {
                    await client.SendEventAsync(item.Payload, cancellationToken);
                }
                catch (Exception exception)
                {
                    item.Attempts += 1;
                    var delaySeconds = Math.Min(3600, Math.Pow(2, Math.Min(item.Attempts, 11)));
                    item.NextAttemptAt = now.AddSeconds(delaySeconds);
                    remaining.Add(item);
                    App.LogException(exception);
                }
            }
            await RewriteAsync(remaining, cancellationToken);
        }
        finally
        {
            _gate.Release();
        }
    }

    private static async Task RewriteAsync(List<OutboxItem> items, CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(PathName)!);
        var temporary = PathName + ".tmp";
        var lines = items.Select(item => JsonSerializer.Serialize(item, Json));
        await File.WriteAllLinesAsync(temporary, lines, cancellationToken);
        File.Move(temporary, PathName, overwrite: true);
    }
}
