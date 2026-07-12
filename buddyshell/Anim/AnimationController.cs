using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace BuddyShell.Anim;

public sealed class AnimationController : IAnimationController, IAnimationDiagnostics
{
    private readonly AnimationManifest _manifest;
    private readonly IAnimationRenderer _renderer;
    private readonly IAnimationClock _clock;
    private readonly DispatcherTimer? _timer;
    private readonly List<QueuedRequest> _queue = [];
    private BaselineSnapshot _baselineSnapshot = new(false, false, false, "idle", new(false, false, false, false), 0.5);
    private AnimationPlan _desiredBaseline;
    private AnimationPlan? _currentPlan;
    private AnimationPhasePlan? _currentPhase;
    private AnimationExecutionKind? _execution;
    private AnimationRequest? _currentRequest;
    private AnimationRequest? _pendingThink;
    private AnimationPlan? _nextBaseline;
    private string? _establishedBaselineId;
    private long _phaseStartedAt;
    private long _generation;
    private long _queueSequence;
    private string? _lastSignature;
    private string? _faultedSignature;
    private long? _faultStartedAt;
    private string? _transitionPlanId;
    private string? _transitionPhase;
    private bool _thinkBodyEntered;
    private string _lastIdleHint = "idle";
    private bool _disposed;

    public AnimationController(
        AnimationManifest manifest,
        IAnimationRenderer renderer,
        IAnimationClock? clock = null,
        bool autoStart = true)
    {
        _manifest = manifest;
        _renderer = renderer;
        _clock = clock ?? new SystemAnimationClock();
        _desiredBaseline = manifest.ResolveBaseline(_baselineSnapshot);
        _renderer.TouchStarted += OnTouchStarted;
        _renderer.TouchDetected += OnTouchDetected;
        _renderer.SetWarmth(_baselineSnapshot.Warmth);
        StartBaselineResolution();

        if (autoStart)
        {
            _timer = new DispatcherTimer(DispatcherPriority.Render)
            {
                Interval = TimeSpan.FromMilliseconds(16),
            };
            _timer.Tick += OnTimerTick;
            _timer.Start();
        }
    }

    public UIElement View => _renderer.View;
    public event EventHandler<TouchDetectedEventArgs>? TouchDetected;
    public event EventHandler<AnimationFaultEventArgs>? Faulted;

    public AnimationSnapshot Snapshot => new(
        _generation,
        _currentPlan?.Id,
        _currentPhase?.Kind,
        _execution,
        _currentRequest?.CorrelationId,
        _desiredBaseline.Id,
        _pendingThink is not null,
        _queue.Count,
        _currentPhase?.FrameCount ?? 0,
        _currentPhase is not null);

    public string HostName => $"{nameof(AnimationController)}/{(_renderer as IAnimationDiagnostics)?.HostName ?? _renderer.GetType().Name}";
    public string AssetRoot => _manifest.PetRoot;
    public int CurrentFrameCount => Snapshot.CurrentFrameCount;
    public bool IsPlaying => Snapshot.IsPlaying;
    public TouchZone ClassifyTouch(double y, double height) =>
        (_renderer as IAnimationDiagnostics)?.ClassifyTouch(y, height)
        ?? (y <= height * 0.45 ? TouchZone.Head : TouchZone.Body);

    public void UpdateBaseline(BaselineSnapshot snapshot) => OnDispatcher(() =>
    {
        if (_disposed) return;
        _baselineSnapshot = snapshot;
        _renderer.SetWarmth(snapshot.Warmth);
        if (!snapshot.StateAvailable)
        {
            Log("baseline_held", "reason=state_unavailable");
            return;
        }
        var idleHint = snapshot.IdleHint.Trim().ToLowerInvariant();
        if (!snapshot.Sleeping && !snapshot.WorkSessionActive && idleHint == "stretch" && _lastIdleHint != "stretch")
        {
            SubmitCore(new AnimationRequest(
                AnimationIntent.Stretch,
                AnimationSource.State,
                $"idle-flourish:{++_queueSequence}",
                AnimationPriority.IdleFlourish));
        }
        _lastIdleHint = idleHint;
        var target = _manifest.ResolveBaseline(snapshot);
        if (target.Id == _desiredBaseline.Id) return;

        Log("baseline_changed", $"from={_desiredBaseline.Id} to={target.Id}");
        _desiredBaseline = target;
        if (_faultStartedAt is not null && _nextBaseline is not null)
        {
            _nextBaseline = target;
        }
        if (_execution == AnimationExecutionKind.Baseline && _faultStartedAt is null)
        {
            StartBaselineResolution();
        }
    });

    public void Submit(AnimationRequest request) => OnDispatcher(() => SubmitCore(request));

    public void Complete(string correlationId, AnimationOutcome outcome) => OnDispatcher(() =>
    {
        if (_disposed || _pendingThink?.CorrelationId != correlationId)
        {
            Log("completion_ignored", $"correlation_id={correlationId}");
            return;
        }

        var pending = _pendingThink;
        _pendingThink = null;
        _queue.RemoveAll(item => item.Request.CorrelationId == correlationId);
        if (outcome.Reaction is { } reaction && reaction != AnimationIntent.Think)
        {
            Enqueue(new AnimationRequest(
                reaction,
                AnimationSource.BridgeResponse,
                correlationId + ":reaction",
                AnimationPriority.Response));
        }

        Log("pending_completed", $"correlation_id={correlationId} error={outcome.IsError} reason={outcome.Reason}");
        if (_execution == AnimationExecutionKind.Pending && _currentRequest?.CorrelationId == correlationId)
        {
            if (_currentPlan?.Exit is { } exit) StartPhase(exit, "pending_complete");
            else FinishCurrent();
        }
        else if (_currentPlan is null && pending is not null)
        {
            ResumeNext();
        }
    });

    public void Tick()
    {
        if (_disposed || _currentPlan is null || _currentPhase is null) return;
        var effectiveNow = _faultStartedAt ?? _clock.ElapsedMilliseconds;
        var elapsed = Math.Max(0, effectiveNow - _phaseStartedAt);
        if (AnimationTimeline.IsComplete(_currentPhase, elapsed))
        {
            AdvancePhase();
            return;
        }

        var frame = AnimationTimeline.Compose(_generation, _currentPlan.Id, _currentPhase, elapsed);
        if (frame.Signature == _lastSignature) return;
        try
        {
            _renderer.Render(frame);
            _lastSignature = frame.Signature;
            if (_faultedSignature is not null)
            {
                if (_faultStartedAt is { } faultStartedAt)
                {
                    _phaseStartedAt += Math.Max(0, _clock.ElapsedMilliseconds - faultStartedAt);
                }
                _faultStartedAt = null;
                _faultedSignature = null;
                Log("recovered", $"generation={_generation} plan={_currentPlan.Id} phase={_currentPhase.Kind}");
                Faulted?.Invoke(this, new AnimationFaultEventArgs(null, recovered: true));
                if (_execution == AnimationExecutionKind.Baseline && _currentPhase?.Kind == AnimationPhaseKind.Body)
                {
                    if (_currentPlan?.Id != _desiredBaseline.Id) StartBaselineResolution();
                    else if (_queue.Count > 0 || _pendingThink is not null) ResumeNext();
                }
            }
        }
        catch (Exception exception)
        {
            if (_faultedSignature != frame.Signature)
            {
                _faultedSignature = frame.Signature;
                _faultStartedAt ??= _clock.ElapsedMilliseconds;
                Log("fault", $"generation={_generation} plan={_currentPlan.Id} phase={_currentPhase.Kind} error={exception.GetType().Name}");
                App.LogException(exception);
                Faulted?.Invoke(this, new AnimationFaultEventArgs(exception, recovered: false));
            }
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (_timer is not null)
        {
            _timer.Stop();
            _timer.Tick -= OnTimerTick;
        }
        _renderer.TouchStarted -= OnTouchStarted;
        _renderer.TouchDetected -= OnTouchDetected;
        _renderer.Dispose();
    }

    private void SubmitCore(AnimationRequest request)
    {
        if (_disposed) return;
        if (request.Intent == AnimationIntent.Think && request.Source != AnimationSource.Chat)
        {
            Log("request_dropped", $"reason=think_requires_chat correlation_id={request.CorrelationId}");
            return;
        }
        if (request.Intent == AnimationIntent.Think)
        {
            if (_pendingThink is not null)
            {
                Log("deduplicated", $"reason=think_already_pending correlation_id={request.CorrelationId}");
                return;
            }
            _pendingThink = request;
            _thinkBodyEntered = false;
        }

        if (IsDuplicate(request))
        {
            Log("deduplicated", $"reason=duplicate intent={request.Intent} correlation_id={request.CorrelationId}");
            return;
        }
        if (_faultStartedAt is not null)
        {
            Enqueue(request);
            Log("queued", $"reason=renderer_fault intent={request.Intent} correlation_id={request.CorrelationId}");
            return;
        }

        if (_currentRequest?.Source == AnimationSource.Feed && request.Source == AnimationSource.Touch)
        {
            Log("deduplicated", $"reason=feed_lock intent={request.Intent} correlation_id={request.CorrelationId}");
            return;
        }
        if (request.Priority == AnimationPriority.IdleFlourish &&
            (_execution != AnimationExecutionKind.Baseline || _currentPhase?.Kind != AnimationPhaseKind.Body))
        {
            Log("request_dropped", $"reason=stale_idle_flourish correlation_id={request.CorrelationId}");
            return;
        }

        if (_currentPlan is null)
        {
            StartRequest(request);
            return;
        }

        if (_execution == AnimationExecutionKind.Baseline)
        {
            if (_currentPhase?.Kind is AnimationPhaseKind.Entry or AnimationPhaseKind.Exit &&
                request.Priority <= AnimationPriority.WorkTransition)
            {
                Enqueue(request);
            }
            else
            {
                StartRequest(request);
            }
            return;
        }

        var currentPriority = _currentRequest?.Priority ?? AnimationPriority.Baseline;
        if (request.Priority > currentPriority)
        {
            Log("request_preempted", $"old={_currentPlan.Id} new_intent={request.Intent}");
            StartRequest(request, "local_preempt");
            return;
        }

        Enqueue(request);
    }

    private bool IsDuplicate(AnimationRequest request)
    {
        if (_currentRequest?.CorrelationId == request.CorrelationId ||
            _queue.Any(item => item.Request.CorrelationId == request.CorrelationId)) return true;
        if (request.Source is AnimationSource.Touch or AnimationSource.Feed)
        {
            return _currentRequest?.Intent == request.Intent ||
                _queue.Any(item => item.Request.Intent == request.Intent && item.Request.Source == request.Source);
        }
        return false;
    }

    private void Enqueue(AnimationRequest request)
    {
        if (IsDuplicate(request)) return;
        _queue.Add(new QueuedRequest(request, ++_queueSequence));
        Log("queued", $"intent={request.Intent} correlation_id={request.CorrelationId} queued={_queue.Count}");
    }

    private void StartRequest(AnimationRequest request, string reason = "request_start")
    {
        InvalidateBaselineScene(request);
        var plan = _manifest.Resolve(request);
        _currentPlan = plan;
        _currentRequest = request;
        _execution = plan.IsPending ? AnimationExecutionKind.Pending : AnimationExecutionKind.Transient;
        _nextBaseline = null;
        Log("plan_started", $"plan={plan.Id} execution={_execution} correlation_id={request.CorrelationId}");
        StartPhase(plan.Entry ?? plan.Body, reason);
    }

    private void InvalidateBaselineScene(AnimationRequest request)
    {
        if (_execution != AnimationExecutionKind.Baseline || _establishedBaselineId is null) return;

        var established = _manifest.Get(_establishedBaselineId);
        if (established.Entry is null) return;

        // Every request plan is rendered as a complete 500x500 scene. Once it replaces a
        // phased baseline (desk, blanket, book, etc.), that baseline's B frame can no longer
        // be resumed in isolation: its A phase must rebuild the scene first.
        Log(
            "baseline_scene_invalidated",
            $"plan={established.Id} by_intent={request.Intent} correlation_id={request.CorrelationId}");
        _establishedBaselineId = null;
    }

    private void StartBaselineResolution()
    {
        var target = _desiredBaseline;
        if (_establishedBaselineId == target.Id)
        {
            StartBaseline(target, resumeBody: true);
            return;
        }

        if (_establishedBaselineId is { } previousId)
        {
            var previous = _manifest.Get(previousId);
            if (previous.Exit is { } exit)
            {
                _currentPlan = previous;
                _currentRequest = null;
                _execution = AnimationExecutionKind.Baseline;
                _nextBaseline = target;
                Log("baseline_exit_started", $"from={previous.Id} to={target.Id}");
                StartPhase(exit, "baseline_changed");
                return;
            }
        }

        StartBaseline(target, resumeBody: false);
    }

    private void StartBaseline(AnimationPlan plan, bool resumeBody)
    {
        _currentPlan = plan;
        _currentRequest = null;
        _execution = AnimationExecutionKind.Baseline;
        _nextBaseline = null;
        if (resumeBody || plan.Entry is null) _establishedBaselineId = plan.Id;
        Log("baseline_started", $"plan={plan.Id} resume_body={resumeBody}");
        if (resumeBody) Log("resume", $"target={plan.Id} reason=baseline");
        StartPhase(
            resumeBody ? plan.Body : plan.Entry ?? plan.Body,
            resumeBody ? "baseline_resume" : "baseline_enter");
    }

    private void StartPhase(AnimationPhasePlan phase, string reason)
    {
        var fromPlan = _transitionPlanId ?? "none";
        var fromPhase = _transitionPhase ?? "none";
        _currentPhase = phase;
        _phaseStartedAt = _clock.ElapsedMilliseconds;
        _generation += 1;
        _lastSignature = null;
        if (_execution == AnimationExecutionKind.Pending && phase.Kind == AnimationPhaseKind.Body)
        {
            _thinkBodyEntered = true;
        }
        var stopwatch = Stopwatch.StartNew();
        Tick();
        stopwatch.Stop();
        var toPlan = _currentPlan?.Id ?? "none";
        var toPhase = PhaseLabel(phase);
        var source = (_currentRequest?.Source ?? AnimationSource.State).ToString().ToLowerInvariant();
        var priority = (int)(_currentRequest?.Priority ??
            (phase.Kind is AnimationPhaseKind.Entry or AnimationPhaseKind.Exit
                ? AnimationPriority.WorkTransition
                : AnimationPriority.Baseline));
        App.LogMessage(
            $"event=animation_transition generation={_generation} request_id={_currentRequest?.CorrelationId ?? "-"} " +
            $"correlation_id={_currentRequest?.CorrelationId ?? "-"} source={source} " +
            $"from_plan={fromPlan} from_phase={fromPhase} to_plan={toPlan} to_phase={toPhase} " +
            $"reason={reason} priority={priority} resume_target={_desiredBaseline.Id} " +
            $"folder={RelativeFolder(phase)} frame_count={phase.FrameCount} duration_ms={phase.DurationMs} " +
            $"first_frame_latency_ms={stopwatch.Elapsed.TotalMilliseconds:F2}");
        _transitionPlanId = toPlan;
        _transitionPhase = toPhase;
    }

    private void AdvancePhase()
    {
        if (_currentPlan is null || _currentPhase is null) return;
        switch (_currentPhase.Kind)
        {
            case AnimationPhaseKind.Entry:
                if (_execution == AnimationExecutionKind.Baseline) _establishedBaselineId = _currentPlan.Id;
                StartPhase(_currentPlan.Body, "phase_complete");
                if (_execution == AnimationExecutionKind.Baseline && _currentPlan.Id != _desiredBaseline.Id)
                {
                    StartBaselineResolution();
                }
                else if (_execution == AnimationExecutionKind.Baseline && (_queue.Count > 0 || _pendingThink is not null))
                {
                    ResumeNext();
                }
                break;
            case AnimationPhaseKind.Body:
                if (_execution == AnimationExecutionKind.Pending) return;
                if (_currentPlan.Exit is { } exit) StartPhase(exit, "phase_complete");
                else FinishCurrent();
                break;
            case AnimationPhaseKind.Exit:
                if (_execution == AnimationExecutionKind.Baseline && _nextBaseline is { } target)
                {
                    _establishedBaselineId = null;
                    StartBaseline(target, resumeBody: false);
                }
                else
                {
                    FinishCurrent();
                }
                break;
        }
    }

    private void FinishCurrent()
    {
        Log("complete", $"plan={_currentPlan?.Id} correlation_id={_currentRequest?.CorrelationId}");
        _currentPlan = null;
        _currentPhase = null;
        _currentRequest = null;
        _execution = null;
        _nextBaseline = null;
        ResumeNext();
    }

    private void ResumeNext()
    {
        if (_queue.Count > 0)
        {
            var next = _queue
                .OrderByDescending(item => item.Request.Priority)
                .ThenBy(item => item.Sequence)
                .First();
            _queue.Remove(next);
            StartRequest(next.Request, "queued_resume");
            return;
        }

        if (_pendingThink is { } pending)
        {
            var plan = _manifest.Resolve(pending);
            _currentPlan = plan;
            _currentRequest = pending;
            _execution = AnimationExecutionKind.Pending;
            Log("pending_resumed", $"correlation_id={pending.CorrelationId} body={_thinkBodyEntered}");
            Log("resume", $"target={plan.Id} reason=pending correlation_id={pending.CorrelationId}");
            StartPhase(_thinkBodyEntered ? plan.Body : plan.Entry ?? plan.Body, "pending_resume");
            return;
        }

        StartBaselineResolution();
    }

    private void OnTouchStarted(object? sender, TouchDetectedEventArgs args)
    {
        Submit(new AnimationRequest(
            args.Zone == TouchZone.Head ? AnimationIntent.TouchHeadReflex : AnimationIntent.TouchBodyReflex,
            AnimationSource.Touch,
            args.CorrelationId,
            AnimationPriority.Touch));
    }

    private void OnTouchDetected(object? sender, TouchDetectedEventArgs args) => TouchDetected?.Invoke(this, args);
    private void OnTimerTick(object? sender, EventArgs args) => Tick();

    private void OnDispatcher(Action action)
    {
        var dispatcher = _renderer.View.Dispatcher;
        if (dispatcher.CheckAccess()) action();
        else dispatcher.Invoke(action);
    }

    private string RelativeFolder(AnimationPhasePlan phase)
    {
        var path = Path.GetDirectoryName(phase.Layers.First().Frames.First().Path) ?? _manifest.PetRoot;
        return Path.GetRelativePath(_manifest.PetRoot, path).Replace('\\', '/');
    }

    private static string PhaseLabel(AnimationPhasePlan phase) =>
        phase.Loop ? "loop" : phase.Kind.ToString().ToLowerInvariant();

    private static void Log(string eventName, string detail) => App.LogMessage($"event=animation_{eventName} {detail}");
    private sealed record QueuedRequest(AnimationRequest Request, long Sequence);
}
