# World Topology

`graphene-instance-topology.json` inventories every root UDT instance as an asset and adds deterministic zone/system metadata. Relations include `contains`, `locatedIn`, `serves/servedBy`, `feeds/fedBy`, and `networkParent`. The current topology builder deliberately keeps inference conservative; it never renames or reparents Graphene browse paths.
