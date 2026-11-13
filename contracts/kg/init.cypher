// SANDMAN KG initialization (idempotent at startup of each writer; executed by the kg-migrate service).
// Traverse (openCypher) syntax. A unique constraint creates its index; CREATE INDEX and CREATE VECTOR INDEX have no IF NOT EXISTS,
// so the clients (KgClient.init_schema / initSchema) ignore an 'already exists' error of these statements.
CREATE CONSTRAINT c_Project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Ingestion_id IF NOT EXISTS FOR (n:Ingestion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Artifact_id IF NOT EXISTS FOR (n:Artifact) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_ArtifactVersion_id IF NOT EXISTS FOR (n:ArtifactVersion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Element_id IF NOT EXISTS FOR (n:Element) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Concept_id IF NOT EXISTS FOR (n:Concept) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_TraceLink_id IF NOT EXISTS FOR (n:TraceLink) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Inconsistency_id IF NOT EXISTS FOR (n:Inconsistency) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT c_Run_id IF NOT EXISTS FOR (n:Run) REQUIRE n.id IS UNIQUE;
CREATE INDEX FOR (n:Artifact) ON (n.projectId);
CREATE INDEX FOR (n:Element) ON (n.key);
CREATE INDEX FOR (n:Element) ON (n.projectId);
CREATE INDEX FOR (n:TraceLink) ON (n.projectId);
CREATE INDEX FOR (n:Inconsistency) ON (n.fingerprint);
CREATE INDEX FOR (n:Inconsistency) ON (n.projectId);
CREATE INDEX FOR (n:Run) ON (n.inputKey);
CREATE CONSTRAINT c_SchemaVersion_id IF NOT EXISTS FOR (n:SchemaVersion) REQUIRE n.id IS UNIQUE;
MERGE (v:SchemaVersion {id: 'kg'}) ON CREATE SET v.version = 1, v.appliedAt = toString(localDateTime());
CREATE VECTOR INDEX element_embedding FOR (n:Element) ON (n.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 2560, `vector.similarity_function`: 'cosine'}};
MATCH (v:SchemaVersion {id: 'kg'}) WHERE v.version < 2 SET v.version = 2;
MATCH (v:SchemaVersion {id: 'kg'}) WHERE v.version < 3 SET v.version = 3;
