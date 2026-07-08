using System;
using System.Collections.Generic;

namespace MyBuddy.VPetPlugin;

public sealed class ActionMapper
{
    private readonly Dictionary<string, string> _actions = new(StringComparer.OrdinalIgnoreCase)
    {
        ["talk"] = "talk",
        ["happy"] = "happy",
        ["comfort"] = "comfort",
        ["concern"] = "concern",
        ["safety"] = "serious",
        ["thinking"] = "thinking",
        ["greet"] = "greet",
        ["remind"] = "alert",
        ["notify"] = "notify",
        ["react"] = "react",
        ["idle"] = "idle",
    };

    private readonly Dictionary<string, string> _expressions = new(StringComparer.OrdinalIgnoreCase)
    {
        ["neutral"] = "neutral",
        ["smile"] = "smile",
        ["happy"] = "happy",
        ["worried"] = "worried",
        ["alert"] = "alert",
        ["serious"] = "serious",
        ["thinking"] = "thinking",
        ["curious"] = "curious",
    };

    public HostAction Map(string? backendAction, string? backendExpression)
    {
        var action = Lookup(_actions, backendAction, "talk");
        var expression = Lookup(_expressions, backendExpression, "neutral");
        return new HostAction(action, expression);
    }

    public void OverrideAction(string backendAction, string hostAction)
    {
        if (!string.IsNullOrWhiteSpace(backendAction) && !string.IsNullOrWhiteSpace(hostAction))
        {
            _actions[backendAction.Trim()] = hostAction.Trim();
        }
    }

    public void OverrideExpression(string backendExpression, string hostExpression)
    {
        if (!string.IsNullOrWhiteSpace(backendExpression) && !string.IsNullOrWhiteSpace(hostExpression))
        {
            _expressions[backendExpression.Trim()] = hostExpression.Trim();
        }
    }

    private static string Lookup(Dictionary<string, string> mapping, string? key, string fallback)
    {
        if (string.IsNullOrWhiteSpace(key))
        {
            return fallback;
        }
        return mapping.TryGetValue(key.Trim(), out var mapped) ? mapped : fallback;
    }
}
