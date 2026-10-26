# mpmath-839 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/mpmath-839:eradyn-agent-gpt validate
#
FROM python:3.12.3-slim

# both revisions, as the reproduction left them
#   /workspace/buggy  f04fa498d9473e50e652c18825e335af3da6ee96   (+ the developer test patch applied)
#   /workspace/fixed  d5c8e0c6174ea006a12255252c598b6c8be7749c   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.3 (main, Apr 15 2024, 18:25:56) [Clang 17.0.6 ]
#   numpy    2.5.4
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
