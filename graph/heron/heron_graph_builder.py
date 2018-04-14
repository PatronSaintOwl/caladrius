""" This module contains classes and methods for constructing graph
representations of Heron logical and physical plans within the Caladrius Graph
Database."""

import logging

from typing import List, Dict, Union

from gremlin_python.process.traversal import P
from gremlin_python.structure.graph import Graph, Vertex
from gremlin_python.driver.driver_remote_connection \
        import DriverRemoteConnection

from caladrius.graph.heron.heron_tracker \
        import get_logical_plan, get_physical_plan, parse_instance_name

LOG: logging.Logger = logging.getLogger(__name__)

class HeronGraphBuilder(object):

    def __init__(self, config: dict) -> None:
        self.config = config
        self.tracker_url = config["heron.tracker.url"]
        self.graph_db_url = config["caladrius.graph.db.url"]
        self.connect()

    def connect(self) -> None:

        LOG.info("Connecting to graph database at: %s", self.graph_db_url)

        self.graph = Graph()
        self.graph_traversal = self.graph.traversal().withRemote(
            DriverRemoteConnection(f"ws://{self.graph_db_url}/gremlin", 'g'))

    def _create_stream_managers(self, topology_id: str, topology_ref: str,
                                physical_plan: Dict[str, Union[str, int]],
                               ) -> None:

        LOG.info("Creating stream managers and container vertices")

        for stream_manager in physical_plan["stmgrs"].values():

            # Create the stream manager vertex
            LOG.debug("Creating vertex for stream manager: %s",
                      stream_manager["id"])

            strmg: Vertex = (self.graph_traversal.addV("stream_manager")
                             .property("id", stream_manager["id"])
                             .property("host", stream_manager["host"])
                             .property("port", stream_manager["port"])
                             .property("shell_port",
                                       stream_manager["shell_port"])
                             .property("topology_id", topology_id)
                             .property("topology_ref", topology_ref)
                             .next())

            # Create the stream manager vertex
            container: int = int(stream_manager["id"].split("-")[1])

            LOG.debug("Creating vertex for container: %d", container)

            cont: Vertex = (self.graph_traversal.addV("container")
                            .property("id", container)
                            .property("topology_id", topology_id)
                            .property("topology_ref", topology_ref)
                            .next())

            # Connect the stream manager to the container
            LOG.debug("Connecting stream manager %s to be within container %d",
                      stream_manager["id"], container)
            (self.graph_traversal.V(strmg).addE("is_within").to(cont).next())

    def _create_spouts(self, topology_id: str, topology_ref: str,
                       physical_plan: Dict[str, Union[str, int]],
                       logical_plan: Dict[str, Union[str, int]]) -> None:

        # Create the spouts
        physical_spouts: Dict[str, List[str]] = physical_plan["spouts"]

        for spout_name, spout_data in logical_plan["spouts"].items():
            LOG.debug("Creating vertices for instances of spout component: %s",
                      spout_name)
            for instance_name in physical_spouts[spout_name]:

                instance: Dict[str, Union[str, int]] = \
                        parse_instance_name(instance_name)

                LOG.debug("Creating vertex for instance: %s", instance_name)

                stream_manager_id: str = \
                    physical_plan["instances"][instance_name]["stmgrId"]

                spout: Vertex = (self.graph_traversal.addV("spout")
                                 .property("container", instance["container"])
                                 .property("task_id", instance["task_id"])
                                 .property("component", spout_name)
                                 .property("stream_manager", stream_manager_id)
                                 .property("spout_type",
                                           spout_data["spout_type"])
                                 .property("spout_source",
                                           spout_data["spout_source"])
                                 .property("topology_id", topology_id)
                                 .property("topology_ref", topology_ref)
                                 .next())

                # Connect the spout to its container vertex
                (self.graph_traversal.V(spout).addE("is_within")
                 .to(self.graph_traversal.V()
                     .hasLabel("container")
                     .has("topology_id", topology_id)
                     .has("topology_ref", topology_ref)
                     .has("id", instance["container"])
                    )
                 .next())


    def _create_bolts(self, topology_id: str, topology_ref: str,
                      physical_plan: Dict[str, Union[str, int]],
                      logical_plan: Dict[str, Union[str, int]]) -> None:

        # Create all the bolt vertices
        physical_bolts: Dict[str, List[str]] = physical_plan["bolts"]

        for bolt_name in logical_plan["bolts"]:
            LOG.debug("Creating vertices for instances of bolt component: %s",
                      bolt_name)
            for instance_name in physical_bolts[bolt_name]:

                instance: Dict[str, Union[str, int]] = \
                        parse_instance_name(instance_name)

                LOG.debug("Creating vertex for bolt instance: %s",
                          instance_name)

                stream_manager_id: str = \
                    physical_plan["instances"][instance_name]["stmgrId"]

                bolt: Vertex = (self.graph_traversal.addV("bolt")
                                .property("container", instance["container"])
                                .property("task_id", instance["task_id"])
                                .property("component", bolt_name)
                                .property("stream_manager", stream_manager_id)
                                .property("topology_id", topology_id)
                                .property("topology_ref", topology_ref)
                                .next())

                # Connect the bolt to its container vertex
                (self.graph_traversal.V(bolt).addE("is_within")
                 .to(self.graph_traversal.V()
                     .hasLabel("container")
                     .has("topology_id", topology_id)
                     .has("topology_ref", topology_ref)
                     .has("id", instance["container"])
                    )
                 .next())

    def _create_logical_connections(self, topology_id: str, topology_ref: str,
                                    logical_plan: Dict[str, Union[str, int]]
                                   ) -> None:

        # Add all the logical connections between the topology's instances
        LOG.info("Adding logical connections to topology %s instances",
                 topology_id)

        for bolt_name, bolt_data in logical_plan["bolts"].items():

            LOG.debug("Adding logical connections for instances of bolt: %s",
                      bolt_name)

            # Get a list of all instance vertices for this bolt
            destination_instances: List[Vertex] = (
                self.graph_traversal.V()
                .has("topology_id", topology_id)
                .has("topology_ref", topology_ref)
                .has("component", bolt_name)
                .toList())

            for incoming_stream in bolt_data["inputs"]:
                source_instances: List[Vertex] = (
                    self.graph_traversal.V()
                    .has("topology_id", topology_id)
                    .has("topology_ref", topology_ref)
                    .has("component", incoming_stream["component_name"])
                    .toList())

                for destination in destination_instances:
                    for source in source_instances:
                        (self.graph_traversal.V(source)
                         .addE("logically_connected")
                         .property("stream_name",
                                   incoming_stream["stream_name"])
                         .property("grouping", incoming_stream["grouping"])
                         .to(destination).next())

    def _create_physical_connections(self, topology_id: str, topology_ref: str,
                                     physical_plan: Dict[str, Union[str, int]],
                                     logical_plan: Dict[str, Union[str, int]]
                                    ) -> None:

        # First get all logically connected pairs
        logical_pairs: List[Dict[str, Vertex]] = (
            self.graph_traversal.V()
            .has("topology_id", topology_id)
            .has("topology_ref", topology_ref)
            .hasLabel(P.within("bolt", "spout")).as_("source")
            .out("logically_connected").as_("destination")
            .select("source", "destination")
            .toList())

        for pair in logical_pairs:
            source = pair["source"]
            destination = pair["destination"]

            # TODO: Find paths by checking if they share state managers

            # TODO: Add physical connections (using coalesce to first check if
            # the connection exists) along the path.

    def build_topology_graph(self, topology_id: str, topology_ref: str,
                             cluster: str, environ: str):

        LOG.info("Building topology %s from cluster %s, environ %s",
                 topology_id, cluster, environ)

        logical_plan: Dict[str, Union[str, int]] = \
                get_logical_plan(self.tracker_url, cluster, environ,
                                 topology_id)

        physical_plan: Dict[str, Union[str, int]] = \
                get_physical_plan(self.tracker_url, cluster, environ,
                                  topology_id)


        self._create_stream_managers(topology_id, topology_ref, physical_plan)

        self._create_spouts(topology_id, topology_ref, physical_plan,
                            logical_plan)

        self._create_bolts(topology_id, topology_ref, physical_plan,
                           logical_plan)

        self._create_logical_connections(topology_id, topology_ref,
                                         logical_plan)
