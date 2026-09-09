"""Register exactly the reviewed seed snapshots in a fresh, private host journal."""
import argparse
import hashlib
import json

from team_brain.contracts import require
from team_brain.host import open_brain, protected
from team_brain.service import bodyprint


def register(brain, seeds):
    require(len(seeds) == 8 and len({s['node_token'] for s in seeds}) == 8, 'eight_seeds_required')
    require(all(s['subject'] == 'team' for s in seeds), 'seed_subject_mismatch')
    with brain.journal.publication():
        snapshots = []
        for seed in seeds:
            snapshot = brain.api.snapshot(seed['node_token'])
            require(snapshot['title'] == seed['title'] and
                    hashlib.sha256(snapshot['text'].encode()).hexdigest() == seed['raw_sha256'],
                    'approved_seed_changed')
            snapshots.append(snapshot)
        with brain.journal.transaction():
            for table in ('pages', 'proposals', 'locks', 'reads'):
                require(brain.journal.db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0,
                        'bootstrap_requires_fresh_journal')
            for snapshot in snapshots:
                brain.journal.register(snapshot['node_token'], 'team')
                brain.journal.baseline(snapshot['node_token'], bodyprint(snapshot))
    return {'registered': len(snapshots)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--seeds', required=True)
    args = parser.parse_args()
    seeds = json.loads(protected(args.seeds).read_text(encoding='utf-8'))
    brain = open_brain(args.config)
    try:
        print(json.dumps(register(brain, seeds)))
    finally:
        brain.journal.close()


if __name__ == '__main__':
    main()
