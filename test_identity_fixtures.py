"""Local historical test labels, excluded from portable releases and production code."""
import json
from pathlib import Path
import tempfile
import atexit

import hyol_scheduler as hs

_data = json.loads((Path(__file__).parent/'tests/fixtures/legacy_identity_labels.json').read_text(encoding='utf-8'))
ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F = _data['labels']
_temporary_states = []


def legacy_config():
    config = dict(_data['config'])
    folder = tempfile.TemporaryDirectory(prefix='hyol-legacy-regression-')
    _temporary_states.append(folder)
    config.update(AccountMode='tab6',AccountDiscovery='legacy',HistoricalPendingPolicy='preserve',
                  SharedStateDirectory=folder.name,AllowMarketPurchase='0',MarketDirectBuyVerified='0')
    return config


@atexit.register
def _cleanup():
    for folder in _temporary_states: folder.cleanup()
