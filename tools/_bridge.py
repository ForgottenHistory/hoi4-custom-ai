"""Makes the hoi4-bridge client importable (HOI4_BRIDGE_CLIENT, else ../../hoi4-bridge/client next to this repo),
and this repo's hoi4ai package."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.environ.get("HOI4_BRIDGE_CLIENT", os.path.join(HERE, "..", "..", "hoi4-bridge", "client"))
sys.path.insert(0, os.path.abspath(CLIENT))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
