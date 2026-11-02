"""Read-only checks around the normal external-agent match entry point."""
import json
import sys
from pathlib import Path

from analysis.platform_audit.probe_interface import InterfaceProbe


def main():
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    probe = InterfaceProbe()
    probe.destination = destination
    probe.install()

    from sc2bench_env.external.turn import TurnInput
    from sc2bench_env.benchmark.__main__ import main as run

    original = TurnInput.render

    def render(turn, *, first):
        text = original(turn, first=first)
        probe.counts['external_model_inputs'] += 1
        for event in (turn.feedback or {}).get('events', []):
            if event.get('event_id') is not None and event.get('type') != 'demand_accepted':
                probe.check(f"[#{event['event_id']} @" in text, 'external_feedback_event_visible',
                            turn.observation.get('game_time_seconds', 0), event=event)
        return text

    TurnInput.render = render
    code = 1
    try:
        code = run(sys.argv[2:])
        return code
    finally:
        destination.write_text(json.dumps(dict(
            exit_code=code, checks=dict(probe.counts), issues=probe.issues,
            submission_failures=probe.submission_failures,
            attack_centers=probe.attack_centers, placement_failures=probe.placement_failures,
            cast_failures=probe.cast_failures, delayed_failures=probe.delayed_failures,
        ), ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    raise SystemExit(main())
