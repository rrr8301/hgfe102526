# jax-38820 — NumBugBench instance, rebuild recipe
#
# TRANSPARENCY ARTIFACT, NOT THE BUILD THAT PRODUCED THE IMAGE.
#
# The published image is a snapshot of the container in which the reproduction
# was validated, so its environment is the one that actually produced
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS.
# Re-running the steps below resolves dependencies again and may therefore land
# on a DIFFERENT environment — which is the drift this packaging exists to
# avoid. Use this file to understand or modify the instance; use the image to
# reproduce it.
#
#   docker run numbugbench/jax-38820:eradyn-agent-gpt validate
#
FROM python:3.12-slim

# both revisions, as the reproduction left them
#   /workspace/buggy  2ed31913ce07da6d4abbc1c2397b24481d971553   (+ the developer test patch applied)
#   /workspace/fixed  da23cf20a2880667a6f04e818ee58b901fbb5da7   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.15 (main, Oct  6 2026, 02:02:22) [GCC 14.2.0]
#   numpy    2.5.0
#   scipy    1.18.0
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
