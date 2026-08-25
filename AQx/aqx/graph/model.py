from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

FLOW_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Port:
    name: str
    kind: str  # "exec" for now


@dataclass
class Node:
    id: str
    type: str
    x: float
    y: float
    props: Dict[str, Any] = field(default_factory=dict)
    inputs: List[Port] = field(default_factory=list)
    outputs: List[Port] = field(default_factory=list)


@dataclass
class Connection:
    id: str
    from_node: str
    from_port: str
    to_node: str
    to_port: str
    log_message: str = ""  # non-empty means this wire has a Log "tap" attached to it


@dataclass
class Graph:
    nodes: Dict[str, Node] = field(default_factory=dict)
    connections: Dict[str, Connection] = field(default_factory=dict)
    repeat: int = 1  # times to run the whole flow; 0 = infinite

    def add_node(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    def remove_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        for cid in [
            c.id for c in self.connections.values() if c.from_node == node_id or c.to_node == node_id
        ]:
            del self.connections[cid]

    def add_connection(self, conn: Connection) -> Connection:
        self.connections[conn.id] = conn
        return conn

    def remove_connection(self, conn_id: str) -> None:
        self.connections.pop(conn_id, None)

    def outgoing(self, node_id: str, port: str = "out") -> Optional[Connection]:
        for c in self.connections.values():
            if c.from_node == node_id and c.from_port == port:
                return c
        return None

    def outgoing_all(self, node_id: str, port: str = "out") -> List[Connection]:
        """Every connection leaving a given output port, in insertion order - used by
        Connector, whose single "out" port can fan out to several destinations
        (regular exec ports only ever follow the first match via outgoing())."""
        return [c for c in self.connections.values() if c.from_node == node_id and c.from_port == port]

    def to_dict(self) -> dict:
        return {
            "version": FLOW_VERSION,
            "repeat": self.repeat,
            "nodes": [
                {"id": n.id, "type": n.type, "x": n.x, "y": n.y, "props": n.props} for n in self.nodes.values()
            ],
            "connections": [
                {
                    "id": c.id,
                    "from_node": c.from_node,
                    "from_port": c.from_port,
                    "to_node": c.to_node,
                    "to_port": c.to_port,
                    "log_message": c.log_message,
                }
                for c in self.connections.values()
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "Graph":
        from .nodes import make_node

        g = Graph(repeat=data.get("repeat", 1))
        for nd in data.get("nodes", []):
            g.add_node(make_node(nd["type"], nd["id"], nd["x"], nd["y"], nd.get("props", {})))
        for cd in data.get("connections", []):
            g.add_connection(Connection(**cd))
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @staticmethod
    def load(path: Path) -> "Graph":
        return Graph.from_dict(json.loads(path.read_text()))
