"""Makes the hoi4-bridge client, the hoi4-recorder library (hoi4rec) and this repo's hoi4ai package importable.

HOI4_BRIDGE_CLIENT, else ../../hoi4-bridge/client next to this repo; HOI4_RECORDER, else ../../hoi4-recorder."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.environ.get("HOI4_BRIDGE_CLIENT", os.path.join(HERE, "..", "..", "hoi4-bridge", "client"))
RECORDER = os.environ.get("HOI4_RECORDER", os.path.join(HERE, "..", "..", "hoi4-recorder"))
sys.path.insert(0, os.path.abspath(CLIENT))
sys.path.insert(0, os.path.abspath(RECORDER))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
