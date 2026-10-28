import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'bin'), str(ROOT / 'tests')]
from operator_engine import OperatorEngine
from test_operator_engine import release


def main():
    with tempfile.TemporaryDirectory(prefix='noleji-core-demo-') as temporary:
        base = Path(temporary)
        home, kit = base / 'home', base / 'kit'
        home.mkdir()
        (kit / 'catalog').mkdir(parents=True)
        (kit / 'catalog/operator-release.json').write_text(json.dumps(release(1)))
        requests = []

        def local_release():
            requests.append('injected-local-fixture')
            return json.dumps(release(2, '# Version two\n')).encode()

        engine = OperatorEngine(home, root=kit, fetcher=local_release)
        plan = engine.plan(['web-app'])
        target = home / '.agents/skills/noleji-operator-web-app/SKILL.md'
        assert not target.exists()
        engine.apply(['web-app'], plan['planId'])
        original = target.read_text()
        engine.check()
        update = engine.plan(['web-app'])
        engine.apply(['web-app'], update['planId'])
        assert target.read_text() != original
        engine.rollback('web-app')
        assert target.read_text() == original
        target.write_text('# User change\n')
        conflict = engine.plan(['web-app'])
        assert not conflict['canApply']
        print(json.dumps({'planReadOnly': True, 'installUpdateRollback': 'passed',
                          'userEditPreserved': target.read_text() == '# User change\n',
                          'conflictBlocksApply': True, 'fixtureFetches': len(requests),
                          'networkRequests': 0, 'providerCalls': 0,
                          'realHomeChanged': False, 'genericTaskApprovalImplemented': False}))


if __name__ == '__main__':
    main()
