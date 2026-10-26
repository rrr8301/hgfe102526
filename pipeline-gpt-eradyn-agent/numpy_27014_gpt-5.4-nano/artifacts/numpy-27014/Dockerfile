# numpy-27014 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/numpy-27014:eradyn-agent-gpt validate
#
FROM python:3.10.12

# both revisions, as the reproduction left them
#   /workspace/buggy  619b07c346d9f4a5bbd39da5ac28b91800b661da   (+ the developer test patch applied)
#   /workspace/fixed  fc82b556493e224447be3f3f8a0200757f23e2d3   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.10.12 (main, Jul 26 2023, 13:20:36) [Clang 16.0.3 ]
#   numpy    -
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
