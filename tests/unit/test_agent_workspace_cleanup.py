"""Match-local cleanup must support Codex's nested plugin paths on Windows."""
import os
from pathlib import Path

import pytest

from sc2bench_env.paths import remove_agent_workspace


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended-length paths')
def test_cleanup_handles_deep_plugin_files_and_readonly_files(tmp_path):
    record = (tmp_path / 'records' / '261102_101354_TvT_hard_KairosJunctionLE').resolve()
    work = record / 'agent'
    extended = Path('\\\\?\\' + str(work))
    deep = extended / 'codex_clean' / 'plugins' / 'cache'
    while len(str(deep)) < 310:
        deep /= 'plugin-template-directory'
    deep.mkdir(parents=True)
    (deep / 'artifact-template.json').write_text('{}', encoding='utf8')
    readonly = extended / 'readonly.txt'
    readonly.write_text('temporary', encoding='utf8')
    readonly.chmod(0o444)
    saved = record / 'codex_session' / 'rollout.jsonl'
    saved.parent.mkdir()
    saved.write_text('{}\n', encoding='utf8')
    remove_agent_workspace(str(work), record)
    assert not extended.exists()
    assert saved.read_text(encoding='utf8') == '{}\n'


def test_cleanup_preserves_directories_outside_the_episode(tmp_path):
    record = tmp_path / 'episode'
    record.mkdir()
    outside = tmp_path / 'agent'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep', encoding='utf8')
    remove_agent_workspace(str(outside), record)
    assert (outside / 'keep.txt').read_text(encoding='utf8') == 'keep'
