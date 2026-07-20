using System.Text.Json.Serialization;

namespace BuddyShell.Bridge;

public sealed class ShellSettings
{
    public string BridgeUrl { get; set; } = "http://127.0.0.1:8000";
    public string? PetAssetRoot { get; set; }
    public int IdlePauseMinutes { get; set; } = 30;
    public double? WindowLeft { get; set; }
    public double? WindowTop { get; set; }
    public string? EdgeSide { get; set; }
    public double? EdgeTopRatio { get; set; }
    public string? LastShownId { get; set; }
    public string? ProtectedApiKey { get; set; }
}

public sealed class BodyStepRequest
{
    [JsonPropertyName("shown_id")]
    public string? ShownId { get; set; }

    [JsonPropertyName("activity_receipt")]
    public BodyActivityReceipt? ActivityReceipt { get; set; }

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

    [JsonPropertyName("surface")]
    public string Surface { get; set; } = "full";
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

public sealed class BodyActivityReceipt
{
    [JsonPropertyName("activity_id")]
    public string ActivityId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "completed";
    [JsonPropertyName("reason")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Reason { get; set; }

    [JsonPropertyName("motion")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public BodyWalkMotion? Motion { get; set; }
}

public sealed class BodyWalkMotion
{
    [JsonPropertyName("start_left")]
    public double StartLeft { get; set; }
    [JsonPropertyName("start_top")]
    public double StartTop { get; set; }
    [JsonPropertyName("end_left")]
    public double EndLeft { get; set; }
    [JsonPropertyName("end_top")]
    public double EndTop { get; set; }
    [JsonPropertyName("window_width")]
    public double WindowWidth { get; set; }
    [JsonPropertyName("window_height")]
    public double WindowHeight { get; set; }
    [JsonPropertyName("work_left")]
    public double WorkLeft { get; set; }
    [JsonPropertyName("work_top")]
    public double WorkTop { get; set; }
    [JsonPropertyName("work_right")]
    public double WorkRight { get; set; }
    [JsonPropertyName("work_bottom")]
    public double WorkBottom { get; set; }
}

public sealed class BodyActivity
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "read";

    [JsonPropertyName("duration_ms")]
    public int DurationMs { get; set; } = 15_000;
}

public sealed class BodyStepResponse
{
    [JsonPropertyName("activity")]
    public BodyActivity? Activity { get; set; }
    [JsonPropertyName("expression")]
    public PendingBodyExpression? Expression { get; set; }

    [JsonPropertyName("shown_confirmed")]
    public bool ShownConfirmed { get; set; }

    [JsonPropertyName("activity_confirmed")]
    public bool ActivityConfirmed { get; set; }

    [JsonPropertyName("event_status")]
    public string EventStatus { get; set; } = "none";

    [JsonPropertyName("time_status")]
    public string TimeStatus { get; set; } = "not_due";

    [JsonPropertyName("mind_status")]
    public string MindStatus { get; set; } = "not_run";
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
