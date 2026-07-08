using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace MyBuddy.VPetPlugin;

public sealed class BridgeSettings
{
    public string BridgeUrl { get; set; } = "http://127.0.0.1:8000";
    public string BridgeToken { get; set; } = "";
    public bool BodyStateInjection { get; set; }
    public bool TouchEscalation { get; set; }
    public bool PhysicalProactive { get; set; }
    public bool TodayQuiet { get; set; }
    public string? TodayQuietDate { get; set; }
    public int IdlePauseMinutes { get; set; } = 30;
    public int DrainPollSeconds { get; set; } = 20;
    public int PresencePollSeconds { get; set; } = 5;
    public int PhysicalCooldownMinutes { get; set; } = 45;
    public int PhysicalDailyLimit { get; set; } = 12;

    public ClientFlags ToClientFlags() => new()
    {
        BodyStateInjection = BodyStateInjection,
        TouchEscalation = TouchEscalation,
        PhysicalProactive = PhysicalProactive,
    };

    public void SetTodayQuiet(bool enabled, DateTimeOffset? now = null)
    {
        TodayQuiet = enabled;
        TodayQuietDate = enabled ? TodayKey(now ?? DateTimeOffset.Now) : null;
    }

    public void NormalizeTodayQuiet(DateTimeOffset? now = null)
    {
        if (!TodayQuiet)
        {
            TodayQuietDate = null;
            return;
        }

        var today = TodayKey(now ?? DateTimeOffset.Now);
        if (string.IsNullOrWhiteSpace(TodayQuietDate))
        {
            TodayQuietDate = today;
            return;
        }
        if (!string.Equals(TodayQuietDate, today, StringComparison.Ordinal))
        {
            TodayQuiet = false;
            TodayQuietDate = null;
        }
    }

    private static string TodayKey(DateTimeOffset now) => now.ToString("yyyy-MM-dd");
}

public sealed class ClientFlags
{
    [JsonPropertyName("body_state_injection")]
    public bool BodyStateInjection { get; set; }

    [JsonPropertyName("touch_escalation")]
    public bool TouchEscalation { get; set; }

    [JsonPropertyName("physical_proactive")]
    public bool PhysicalProactive { get; set; }
}

public sealed class BodyState
{
    [JsonPropertyName("food")]
    public double? Food { get; set; }

    [JsonPropertyName("drink")]
    public double? Drink { get; set; }

    [JsonPropertyName("feeling")]
    public double? Feeling { get; set; }

    [JsonPropertyName("health")]
    public double? Health { get; set; }

    [JsonPropertyName("strength")]
    public double? Strength { get; set; }

    [JsonPropertyName("likability")]
    public double? Likability { get; set; }

    [JsonPropertyName("money")]
    public double? Money { get; set; }

    [JsonPropertyName("mode")]
    public string? Mode { get; set; }
}

public sealed class VPetChatRequest
{
    [JsonPropertyName("message")]
    public string Message { get; set; } = "";

    [JsonPropertyName("event")]
    public string Event { get; set; } = "chat";

    [JsonPropertyName("body_state")]
    public BodyState? BodyState { get; set; }
}

public sealed class VPetEventRequest
{
    [JsonPropertyName("event")]
    public string Event { get; set; } = "";

    [JsonPropertyName("count")]
    public int Count { get; set; } = 1;

    [JsonPropertyName("body_state")]
    public BodyState? BodyState { get; set; }

    [JsonPropertyName("context")]
    public Dictionary<string, object?>? Context { get; set; }

    [JsonPropertyName("want_reply")]
    public bool WantReply { get; set; }

    [JsonPropertyName("client_event_id")]
    public string? ClientEventId { get; set; }
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
    public List<VPetPendingEvent> Pending { get; set; } = new();
}

public sealed class VPetEventResponse : VPetBridgeResponse
{
    [JsonPropertyName("replied")]
    public bool Replied { get; set; }

    [JsonPropertyName("gate_reason")]
    public string? GateReason { get; set; }

    [JsonPropertyName("event_log_id")]
    public int? EventLogId { get; set; }
}

public sealed class VPetPendingResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("bridge")]
    public string? Bridge { get; set; }

    [JsonPropertyName("drained")]
    public bool Drained { get; set; }

    [JsonPropertyName("events")]
    public List<VPetPendingEvent> Events { get; set; } = new();

    [JsonPropertyName("digest")]
    public VPetDigest? Digest { get; set; }
}

public sealed class VPetStatusResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("configured")]
    public bool Configured { get; set; }

    [JsonPropertyName("model")]
    public string? Model { get; set; }
}

public sealed class VPetPendingEvent
{
    [JsonPropertyName("id")]
    public int? Id { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("speech")]
    public VPetSpeech? Speech { get; set; }

    [JsonPropertyName("action")]
    public VPetAction? Action { get; set; }

    [JsonPropertyName("expression")]
    public VPetExpression? Expression { get; set; }

    [JsonPropertyName("scheduled_at")]
    public string? ScheduledAt { get; set; }
}

public sealed class VPetSpeech
{
    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("interrupt")]
    public bool Interrupt { get; set; }

    [JsonPropertyName("persistent")]
    public bool Persistent { get; set; }
}

public sealed class VPetAction
{
    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("priority")]
    public int Priority { get; set; }

    [JsonPropertyName("reason")]
    public string? Reason { get; set; }
}

public sealed class VPetExpression
{
    [JsonPropertyName("name")]
    public string? Name { get; set; }
}

public sealed class VPetDigest
{
    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("sources")]
    public List<string> Sources { get; set; } = new();

    [JsonPropertyName("discarded_count")]
    public int DiscardedCount { get; set; }
}

public sealed class BridgeRequestException : Exception
{
    public int? StatusCode { get; }

    public BridgeRequestException(string message, int? statusCode = null, Exception? inner = null)
        : base(message, inner)
    {
        StatusCode = statusCode;
    }
}
