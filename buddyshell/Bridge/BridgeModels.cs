using System.Text.Json.Serialization;

namespace BuddyShell.Bridge;

public sealed class ShellSettings
{
    public string BridgeUrl { get; set; } = "http://127.0.0.1:8000";
    public string BridgeToken { get; set; } = "";
    public bool PhysioInjection { get; set; } = true;
    public bool TouchEscalation { get; set; } = true;
    public bool PhysicalProactive { get; set; } = true;
    public bool TodayQuiet { get; set; }
    public string? TodayQuietDate { get; set; }
    public string? PetAssetRoot { get; set; }
    // Legacy JSON compatibility only. The animation route is now fixed to
    // AnimationController + FramePlayerHost and this value is intentionally ignored.
    public bool ForceFramePlayer { get; set; }
    public int IdlePauseMinutes { get; set; } = 30;
    public int PhysicalCooldownMinutes { get; set; } = 45;
    public int PhysicalDailyLimit { get; set; } = 12;
    public string? PhysicalDate { get; set; }
    public int PhysicalCount { get; set; }
    public string? LastPhysicalServerTime { get; set; }
    public double? WindowLeft { get; set; }
    public double? WindowTop { get; set; }
    public string? ActiveWorkSessionId { get; set; }
    public string? LastShownId { get; set; }

    public ClientFlags ToClientFlags() => new()
    {
        PhysioInjection = PhysioInjection,
        TouchEscalation = TouchEscalation,
        PhysicalProactive = PhysicalProactive,
    };

    public void NormalizeTodayQuiet(string serverDate)
    {
        if (!TodayQuiet)
        {
            TodayQuietDate = null;
            return;
        }
        if (string.IsNullOrWhiteSpace(TodayQuietDate))
        {
            TodayQuietDate = serverDate;
        }
        else if (!string.Equals(TodayQuietDate, serverDate, StringComparison.Ordinal))
        {
            TodayQuiet = false;
            TodayQuietDate = null;
        }
    }
}

public sealed class ClientFlags
{
    [JsonPropertyName("physio_injection")]
    public bool PhysioInjection { get; set; }

    [JsonPropertyName("touch_escalation")]
    public bool TouchEscalation { get; set; }

    [JsonPropertyName("physical_proactive")]
    public bool PhysicalProactive { get; set; }
}

public sealed class BodyStepRequest
{
    [JsonPropertyName("shown_id")]
    public string? ShownId { get; set; }

    [JsonPropertyName("event")]
    public BodyEvent? Event { get; set; }
}

public sealed class BodyEvent
{
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "chat";

    [JsonPropertyName("content")]
    public string Content { get; set; } = "";
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
}

public sealed class VPetEventRequest
{
    [JsonPropertyName("event")]
    public string Event { get; set; } = "";

    [JsonPropertyName("count")]
    public int Count { get; set; } = 1;

    [JsonPropertyName("context")]
    public Dictionary<string, object?> Context { get; set; } = [];

    [JsonPropertyName("want_reply")]
    public bool WantReply { get; set; }

    [JsonPropertyName("client_event_id")]
    public string ClientEventId { get; set; } = Guid.NewGuid().ToString("N");
}

public sealed class VPetDrainRequest
{
    [JsonPropertyName("digest")]
    public bool Digest { get; set; }
}

public class VPetBridgeResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("bridge")]
    public string? Bridge { get; set; }

    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("speech")]
    public VPetSpeech? Speech { get; set; }

    [JsonPropertyName("action")]
    public VPetAction? Action { get; set; }

    [JsonPropertyName("expression")]
    public VPetExpression? Expression { get; set; }

    [JsonPropertyName("pending")]
    public List<VPetPendingEvent> Pending { get; set; } = [];

    [JsonPropertyName("turn_id")]
    public string? TurnId { get; set; }
}

public sealed class VPetEventResponse : VPetBridgeResponse
{
    [JsonPropertyName("replied")]
    public bool Replied { get; set; }

    [JsonPropertyName("gate_reason")]
    public string? GateReason { get; set; }

    [JsonPropertyName("event_log_id")]
    public int? EventLogId { get; set; }

    [JsonPropertyName("duration_minutes")]
    public int? DurationMinutes { get; set; }
}

public sealed class VPetPendingResponse
{
    [JsonPropertyName("events")]
    public List<VPetPendingEvent> Events { get; set; } = [];

    [JsonPropertyName("digest")]
    public VPetDigest? Digest { get; set; }

    [JsonPropertyName("server_flags")]
    public ClientFlags ServerFlags { get; set; } = new();
}

public sealed class VPetPendingEvent
{
    [JsonPropertyName("id")]
    public int? Id { get; set; }

    [JsonPropertyName("source")]
    public string Source { get; set; } = "unknown";

    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("speech")]
    public VPetSpeech? Speech { get; set; }

    [JsonPropertyName("action")]
    public VPetAction? Action { get; set; }

    [JsonPropertyName("expression")]
    public VPetExpression? Expression { get; set; }
}

public sealed class VPetSpeech
{
    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("interrupt")]
    public bool Interrupt { get; set; }

    [JsonPropertyName("persistent")]
    public bool Persistent { get; set; }

    [JsonPropertyName("truncated")]
    public bool Truncated { get; set; }
}

public sealed class VPetAction
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "neutral";
}

public sealed class VPetExpression
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "neutral";
}

public sealed class VPetDigest
{
    [JsonPropertyName("text")]
    public string Text { get; set; } = "";
}

public sealed class VPetStateResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("bridge")]
    public string Bridge { get; set; } = "";

    [JsonPropertyName("server_time")]
    public string ServerTime { get; set; } = "";

    [JsonPropertyName("time_offset_minutes")]
    public int TimeOffsetMinutes { get; set; }

    [JsonPropertyName("physio")]
    public PhysioSnapshot? Physio { get; set; }

    [JsonPropertyName("idle_hint")]
    public string IdleHint { get; set; } = "read";

    [JsonPropertyName("warmth")]
    public double Warmth { get; set; }

    [JsonPropertyName("server_flags")]
    public ClientFlags ServerFlags { get; set; } = new();

    [JsonPropertyName("day_index")]
    public int DayIndex { get; set; }
}

public sealed class PhysioSnapshot
{
    [JsonPropertyName("hunger")]
    public int Hunger { get; set; }

    [JsonPropertyName("energy")]
    public int Energy { get; set; }

    [JsonPropertyName("mood")]
    public int Mood { get; set; }

    [JsonPropertyName("sleeping")]
    public bool Sleeping { get; set; }

    [JsonPropertyName("woken")]
    public bool Woken { get; set; }

    [JsonPropertyName("levels")]
    public PhysioLevelFlags Levels { get; set; } = new();
}

public sealed class PhysioLevelFlags
{
    [JsonPropertyName("hungry")]
    public bool Hungry { get; set; }

    [JsonPropertyName("tired")]
    public bool Tired { get; set; }

    [JsonPropertyName("low")]
    public bool Low { get; set; }

    [JsonPropertyName("bright")]
    public bool Bright { get; set; }
}

public sealed class BridgeRequestException(
    string message,
    int? statusCode = null,
    Exception? inner = null) : Exception(message, inner)
{
    public int? StatusCode { get; } = statusCode;
}
