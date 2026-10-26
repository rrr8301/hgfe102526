# scipy-24742 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-24742:eradyn-agent-gpt validate
#
FROM python:3.13.7

# both revisions, as the reproduction left them
#   /workspace/buggy  cbef678e05c5a30b53b7d05606ed3dde074f7b48   (+ the developer test patch applied)
#   /workspace/fixed  0a86361920dc920e8fe9089f5591a9f6ec76d657   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.13.7 (main, Sep 18 2025, 19:47:49) [Clang 20.1.4 ]
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
