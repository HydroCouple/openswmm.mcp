# Ontology context for agents

Generated from the water-ontology schema (version 0.2.0); do not edit. Tables are in catalog `water_core`; the table for a class is shown in brackets. Abstract classes have no table; query the union views `topology.asset` and `model.swmm_element`, or `reference.entity` (every entity with its class).

## Identity and conventions

- Every row has `iri` (the thing) and `class_iri` (what it is). Use IRIs, never labels, as tool arguments; resolve names through `label` and `alt_ids`.
- Connectivity, drainage, containment, attachment, zone membership and model links are rows in `topology.edge` (`from_iri`, `relation`, `to_iri`), never columns on an asset.
- An asset is linked to its SWMM model element by a `REPRESENTED_BY` edge; `topology.*` values are SI (m, ha, mm, m3/s), `model.*` values keep the model's own units.
- Tables with `valid_to` are versioned; read the `<table>_current` view unless the question is historical.
- Every derived value carries `was_generated_by`; cite it (attempt, model version, job) with any number you report.
- Units are QUDT IRIs in a `unit` column or a column tag; never assume a unit.

## Resolving model and operator terminology

Use registry.json bindings to resolve a term in its class and source scheme. Look up rain gauge and rain gage under RainGage. [RAINGAGES] is the input section. Identify an instance with its system, object kind and source ID. Check the mapping predicate and governing profile before treating terms as interchangeable.

## Classes

### core

- Entity (abstract): Anything with a stable IRI.
  - DerivedEntity (abstract): Anything produced by a computation, pipeline or agent.
  - SpatialEntity (abstract): An entity with a location or footprint.
    - Asset (abstract): A physical component of a water system that is owned, maintained and can fail.
  - Utility  [reference.utility]: A water utility or operating organisation.

### provenance

- ArtifactChecksum: An SPDX digest value and algorithm for a distribution.
- Distribution: One concrete form of a dataset-like entity - a Delta table at a version, a file in a Volume, a model artifact.
- (under Entity)
  - Agent  [reference.agent]: Someone or something responsible for an entity or activity (PROV-O Agent).

### topology

- Edge  [topology.edge]: One directed, typed relationship between two entities.
- (under Asset)
  - Inlet  [topology.inlet]: A curb, grate or combination inlet that captures street flow; CAPTURES_TO a Node and is ATTACHED_TO a Street.
  - Link (abstract): A component that conveys flow between two nodes.
    - Conduit  [topology.conduit]: A gravity sewer, force main, culvert or open channel with a cross-section and roughness.
      - Street  [topology.street]: A street cross-section conveying surface flow; inlets along it capture flow to the drainage network.
    - Orifice  [topology.orifice]: A side or bottom orifice regulating flow between nodes; may be gated.
    - Outlet  [topology.outlet]: A flow regulator defined by a rating curve.
    - Pump  [topology.pump]: A pump; connects through SUCTION and DISCHARGE terminals.
    - Weir  [topology.weir]: A transverse, side-flow, V-notch, trapezoidal or roadway weir.
  - Node (abstract): A point in the network where links meet or where flow enters, leaves or is stored.
    - Divider  [topology.divider]: A node that splits inflow between two downstream links by rule, weir or tabular relationship.
    - Junction  [topology.junction]: A manhole, catch basin or pipe junction with no storage volume beyond its shaft.
    - LIDControl  [topology.lid_control]: A low-impact-development control (bioretention, permeable pavement, green roof, ...).
    - Outfall  [topology.outfall]: A terminal node where flow leaves the system to a receiving water; defines a sewershed when used as a subnetwork controller.
    - StorageUnit  [topology.storage_unit]: A wet well, detention basin, retention pond or tank with a depth-area or depth-volume relationship.
  - RainGage  [topology.rain_gage]: A rain gage or the rainfall record it provides; Subcatchments reference it by USES_GAGE edges.
  - Structure  [topology.structure]: A chamber, vault, pump station building or other enclosure that contains devices.
- (under Entity)
  - LandUse  [topology.land_use]: A land-use category used for pollutant buildup and washoff.
  - Terminal  [topology.terminal]: A named port on a device through which it connects.
  - WaterSystem  [topology.water_system]: A hydraulically connected drainage, sewer or stormwater network operated as one system, usually one SWMM model.
- (under SpatialEntity)
  - Aquifer  [topology.aquifer]: A groundwater unit beneath subcatchments or a 2D mesh; exchanges with the drainage network.
  - Cell2D  [topology.cell_2d]: One cell of a Mesh2D.
  - Mesh2D  [topology.mesh_2d]: A finite-volume surface mesh for 2D overland flow coupled to the 1D network.
  - Subcatchment  [topology.subcatchment]: A land area whose runoff drains to one outlet node or subcatchment (DRAINS_TO edge).
  - Zone  [topology.zone]: A derived grouping of nodes, links and subcatchments such as a sewershed, drainage area or CSO basin.

### observation

- (under Asset)
  - Sensor  [observation.sensor]: A physical or virtual device that observes a property of an asset or area.
- (under DerivedEntity)
  - Observation  [observation.observation]: One value of one property for one entity at one time.
  - Setpoint  [observation.setpoint]: A target value for a controllable property (pump speed, gate opening, tank level band) effective over a period.
- (under Entity)
  - Constituent  [observation.constituent]: A substance or aggregate such as TSS whose concentration or load may be modeled.
  - Datastream  [observation.datastream]: One sensor observing one property of one feature of interest over time; the unit of telemetry ingestion and the thing a staleness threshold
  - ObservedProperty  [observation.observed_property]: A measurable property such as flow, depth or TSS concentration, with its physical kind and default unit.

### event

- EventLink  [event.event_link]: A typed link from an event to an asset, zone, observation, work order, attempt or other event.
- (under DerivedEntity)
  - EventStateChange  [event.event_state_change]: One lifecycle transition of an event with who, when and why; the PROV activity behind every state the event has been in.
- (under SpatialEntity)
  - Event  [event.event]: Something that happened to the system.

### operations

- (under Entity)
  - Inspection  [operations.inspection]: A condition inspection of an asset with its grades; may be EVIDENCE_FOR an event such as a blockage.
  - WorkOrder  [operations.work_order]: A unit of work on an asset.

### coupling

- ComponentArgument: One IArgument of a component as configured (name, value or file path, type).
- ComponentConnection  [coupling.component_connection]: A typed link from a provider Output to a consumer Input, optionally through an AdaptedOutput.
- (under Entity)
  - Composition  [coupling.composition]: The set of components and connections run together; what hydrocouple.platform packages into a Revision.
  - ExchangeItem (abstract): An input or output of a component with its value definition (quantity and unit), dimensions and spatial structure.
    - Input  [coupling.input]: A consumer of values.
    - Output  [coupling.output]: A provider of values.
      - AdaptedOutput  [coupling.adapted_output]: An output transformed in time, space or unit from a provider output by an adapter factory.
  - ModelComponent  [coupling.model_component]: A configured instance of a HydroCouple model component inside a composition - a SWMM model, a 2D mesh, a data reader, an adapter factory or

### analysis

- VerifiedClaim: One number a finding's statement cites, checked against the artifact it cites.
- VerifierReport: The verifier's account of a finding - each cited number, where it was looked up and what was found there.
- (under DerivedEntity)
  - AgentFinding  [analysis.agent_finding]: What an agent surfaced to a person, with the trace, tools, evidence and assumptions behind it and the verifier's verdict.
  - Candidate  [analysis.candidate]: One evaluated design or policy in a study, with its decision variables and objective values.
  - ResultArtifact  [analysis.result_artifact]: An output produced by an execution attempt, identified by the digest of its bytes.
  - Run  [analysis.run]: A request to execute a workflow on a revision on a target (platform run request plus the job spec the control plane built from it).
  - Study  [analysis.study]: An ask/tell optimisation over an evaluator pool (platform study view): objectives, budget, state and the Pareto set.
- (under Entity)
  - AgentSession  [analysis.agent_session]: One session of an AI agent - a client and a model that a person started.
  - Attempt  [analysis.attempt]: One execution attempt of a run with its artifacts, provenance and quality (platform attempt manifest).
  - IngestionActivity  [analysis.ingestion_activity]: A particular import of a revision under a versioned mapping profile.
  - InputArtifact  [analysis.input_artifact]: A hashed primary input or an external dependency in a revision bundle.
  - InputRecord  [analysis.input_record]: An original INP record retained with its line number and ordered native tokens.
  - Revision  [analysis.revision]: An immutable model revision identified by its platform manifest or by a local input bundle and its checksum.
  - Tool  [analysis.tool]: A tool an agent can call, as its server registers it.
  - ToolCall  [analysis.tool_call]: One call an agent made to a tool.

### ml

- EvaluationMetric: One metric on one split, with the baseline value it must beat.
- FeatureBinding: Binds one model input or target to the ontology - which class, which property, how aggregated, over what window, reached through which relat
- (under DerivedEntity)
  - DriftCheck  [ml.drift_check]: A scheduled comparison of live inputs or predictions against the training distribution or recent observations (Lakehouse Monitoring); DRIFTE
  - MLModelVersion  [ml.ml_model_version]: One trained artifact of an MLModel, registered in Unity Catalog through MLflow.
  - Prediction  [ml.prediction]: One predicted value for one entity, property and valid time from one model version, with interval or probability, the inputs used and an out
  - TrainingDataset  [ml.training_dataset]: The exact data a version was trained and evaluated on - tables at Delta versions, time window, entities, split strategy - so training is rep
- (under Entity)
  - MLModel  [ml.ml_model]: A named modelling problem with a stable identity across versions, e.g.

### SWMM model elements (generated from the engine catalog)

- SwmmElement (abstract), keyed by `revision` + `element_id`: SwmmNode -> SwmmJunction, SwmmOutfall, SwmmStorage, SwmmDivider; SwmmLink -> SwmmConduit, SwmmPump, SwmmOrifice, SwmmWeir, SwmmOutlet; SwmmSubcatchment; SwmmGage; SwmmPollutant; SwmmLanduse; SwmmPattern; SwmmTable. Columns mirror engine properties and are tagged with `catalog_path`, `access` and `unit_kind`.

## Relations in topology.edge

- `CONNECTS_TO`: Hydraulic connectivity from the upstream end to the downstream end as modelled Allowed: Link -> Node; Node -> Link.
- `DRAINS_TO`: Runoff routing from a Subcatchment to a Node or another Subcatchment Allowed: Subcatchment -> Node; Subcatchment -> Subcatchment; LIDControl -> Node.
- `CAPTURES_TO`: An Inlet captures street flow to a Node Allowed: Inlet -> Node.
- `USES_GAGE`: A Subcatchment receives rainfall from a RainGage Allowed: Subcatchment -> RainGage.
- `CONTAINED_IN`: Structural containment such as a flow meter inside a chamber Allowed: Asset -> Structure; LIDControl -> Subcatchment; Sensor -> Structure.
- `ATTACHED_TO`: A device mounted on another asset such as a level sensor on a manhole Allowed: Sensor -> Asset; Inlet -> Street.
- `BELONGS_TO_ZONE`: Membership of a derived sewershed or drainage area or CSO basin Allowed: Asset -> Zone; Subcatchment -> Zone; Zone -> Zone.
- `FEEDS`: A node or storage that supplies a zone or downstream system Allowed: Node -> Zone; WaterSystem -> WaterSystem.
- `ISOLATED_BY`: A conduit or zone whose isolation depends on this gate or valve Allowed: Conduit -> Orifice; Zone -> Orifice; Conduit -> Pump.
- `PART_OF_SYSTEM`: Membership of a WaterSystem Allowed: Asset -> WaterSystem; Subcatchment -> WaterSystem; Zone -> WaterSystem; Aquifer -> WaterSystem; Mesh2D -> WaterSystem.
- `REPRESENTED_BY`: The SwmmElement in a given Revision that models this asset Allowed: Asset -> SwmmElement; Subcatchment -> SwmmSubcatchment; RainGage -> SwmmGage; WaterSystem -> Revision.
- `COUPLED_TO`: A HydroCouple exchange between two model components or domains Allowed: ModelComponent -> ModelComponent; WaterSystem -> Mesh2D; WaterSystem -> Aquifer.

## Relations in event.event_link

- `AFFECTS`: Asset or zone or cell or customer set impacted Allowed: Event -> Asset; Event -> Zone; Event -> Cell2D.
- `LOCATED_AT`: The asset or location where it occurred Allowed: Event -> Asset; Event -> Subcatchment.
- `EVIDENCE_FOR`: An observation, anomaly or report supporting this event (link from evidence to event) Allowed: Observation -> Event; Inspection -> Event; Prediction -> Event; Event -> Event.
- `CAUSED_BY`: Another event or condition that caused this one Allowed: Event -> Event.
- `PART_OF`: Child event of a larger one (e.g. flooding within a storm) Allowed: Event -> Event.
- `RESOLVED_BY`: Work order or control action that resolved it Allowed: Event -> WorkOrder; Event -> Setpoint.
- `TRIGGERED`: A control action or alarm this event triggered Allowed: Event -> Setpoint; Event -> Event.
- `EXPLAINED_BY`: A model attempt or finding that explains it Allowed: Event -> Attempt; Event -> AgentFinding.
- `REPORTED_IN`: Regulatory report or notice

## Key enumerations

- LifecycleStatus: PROPOSED, UNDER_CONSTRUCTION, UNKNOWN, IN_SERVICE, OUT_OF_SERVICE, ABANDONED, REMOVED
- QualityFlag: GOOD, SUSPECT, BAD, MISSING, ESTIMATED, STALE, OUT_OF_RANGE, MANUAL
- ResultKind: MEASUREMENT, FORECAST, SETPOINT, SIMULATION, AGGREGATE
- EventLifecycleState: DETECTED, CONFIRMED, IN_PROGRESS, RESOLVED, FALSE_POSITIVE, SUPERSEDED
- Severity: INFO, LOW, MEDIUM, HIGH, CRITICAL
- ModelStage: DEVELOPMENT, VALIDATED, CHAMPION, CHALLENGER, ARCHIVED
- AgentKind: PERSON, ORGANIZATION, SERVICE_PRINCIPAL, AI_AGENT, ML_MODEL, SOFTWARE

## Rules the data must satisfy

The validator enforces these; open violations are in `reference.ontology_violation`. If an entity you reason about appears there, say so.

- Both ends of every edge are known entities.
- Every in-service Link has exactly one upstream node (Node CONNECTS_TO Link) and one downstream node (Link CONNECTS_TO Node).
- Every Subcatchment has at least one DRAINS_TO edge.
- Every Subcatchment has at least one USES_GAGE edge.
- Every Sensor has at least one ATTACHED_TO edge.
- Every Asset has at least one PART_OF_SYSTEM edge.
- An asset is REPRESENTED_BY at most one model element per revision.
- An asset with model links is REPRESENTED_BY an element in its own system's current revision.
- A WaterSystem's current_revision is a known Revision.
- A work order that resolves an event is COMPLETED with a completion time.
- A work order proposed by an AI agent is not APPROVED or beyond without a human approver.
- A RESOLVED event has a RESOLVED_BY link.
- CONFIRMED, IN_PROGRESS and RESOLVED events record who confirmed them.
- An AI agent may detect an event but never confirm it.
- Every event is LOCATED_AT or AFFECTS something.
- occurred_at <= detected_at <= ended_at where present.
- A prediction's target entity is an instance of its model's applies_to_class.
- A model has at most one CHAMPION version.
- A model element with an INP source locator points to a record in the same revision.
- An imported SWMM simulation result resolves its source line, attempt, run, input revision, software agent and output artifact.
- A local SWMM bundle records a primary artifact matching its input checksum.
- An observation has an instant or an ordered time interval.
- An input record belongs to an existing revision.
- Everything a finding cites as evidence is a known entity.
- A finding is never reviewed by an AI agent, or by the agent it is attributed to.
- A tool call belongs to a known agent session.
- The agent of an agent session is an AI agent that acts on behalf of someone.
- An observation's unit is its property's default unit (after normalisation).
- Each relation only connects the classes listed under Relations.

## What an agent may and may not do

- May read governed tables and call registered tools; may write `AgentFinding` rows, events in state DETECTED with source AGENT, and proposals (`Setpoint`, `WorkOrder`) that name a human approver field left empty.
- May not confirm events, approve work orders, set setpoints, or issue control actions.
- Must state staleness, non-GOOD quality flags, out-of-scope predictions and non-current model revisions when they affect an answer.
