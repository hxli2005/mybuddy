using System.Text.Json.Serialization;

namespace BuddyShell.Bridge;

public sealed class ShellSettings
{
    public string BridgeUrl { get; set; } = "http://127.0.0.1:8000";
    public string? PetAssetRoot { get; set; }
    public int IdlePauseMinutes { get; set; } = 30;
    public double? WindowLeft { get; set; }
    public double? WindowTop { get; set; }
    public string? LastShownId { get; set; }
}

public sealed class BodyStepRequest
{
    [JsonPropertyName("shown_id")]
    public string? ShownId { get; set; }

    [JsonPropertyName("event")]
    public BodyEvent? Event { get; set; }

    [JsonPropertyName("presence")]
    public BodyPresence? Presence { get; set; }
}

public sealed class BodyPresence
{
    [JsonPropertyName("present")]
    public bool Present { get; set; }

    [JsonPropertyName("fullscreen")]
    public bool Fullscreen { get; set; }
}

public sealed class BodyEvent
{
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "chat";

    [JsonPropertyName("content")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Content { get; set; }
}

public sealed class BodyStepResponse
{
    [JsonPropertyName("baseline")]
    public Dictionary<string, string> Baseline { get; set; } = [];

    [JsonPropertyName("expression")]
    public PendingBodyExpression? Expression { get; set; }

    [JsonPropertyName("shown_confirmed")]
    public bool ShownConfirmed { get; set; }

    [JsonPropertyName("event_status")]
    public string EventStatus { get; set; } = "none";

    [JsonPropertyName("time_status")]
    public string TimeStatus { get; set; } = "not_due";
}

public sealed class PendingBodyExpression
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; set; } = "";

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "direct";
}

public sealed class BridgeRequestException(
    string message,
    int? statusCode = null,
    Exception? inner = null) : Exception(message, inner)
{
    public int? StatusCode { get; } = statusCode;
}
