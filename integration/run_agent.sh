# Change below to your own path
cd /Users/k323lee/git/AI4EC && python3 -m integration.agent integration/tests/fixtures/incomplete_proof.ec \
  --llm-model google/gemma-4-12b-qat \
  --embed-model text-embedding-embeddinggemma-300m \
  --lm-studio-url http://127.0.0.1:1234/v1 \
  --log-file integration/output/incomplete_proof_run_gemma.json \
  --max-premises 400 \
  --max-steps 10