// 003_vector_indexes — native vector indexes on Post.embedding and
// Entity.embedding. ${EMBED_DIM} is substituted by the runner from
// InferenceConfig.embed_dim (default 1024 for bge-m3).

CREATE VECTOR INDEX post_embedding IF NOT EXISTS
  FOR (p:Post) ON (p.embedding)
  OPTIONS {indexConfig: {
    `vector.dimensions`: ${EMBED_DIM},
    `vector.similarity_function`: 'cosine'
  }};

CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
  FOR (e:Entity) ON (e.embedding)
  OPTIONS {indexConfig: {
    `vector.dimensions`: ${EMBED_DIM},
    `vector.similarity_function`: 'cosine'
  }};
