# pandas-64755 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/pandas-64755:eradyn-agent-gpt validate
#
FROM python:3.12

# both revisions, as the reproduction left them
#   /workspace/buggy  23f61296bcbf54849cdb2e38994b810ccc3a7446   (+ the developer test patch applied)
#   /workspace/fixed  8d1f9c941400dba54820c1ab4c9776c1089fec7a   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.4 (main, Jul 25 2024, 22:42:01) [Clang 18.1.8 ]
#   numpy    2.2.1
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
