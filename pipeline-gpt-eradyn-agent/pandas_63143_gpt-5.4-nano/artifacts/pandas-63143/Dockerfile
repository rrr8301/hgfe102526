# pandas-63143 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/pandas-63143:eradyn-agent-gpt validate
#
FROM python:3.13

# both revisions, as the reproduction left them
#   /workspace/buggy  eaabb41d17ca5757e8a4c0f3a2f1914a688bf3bd   (+ the developer test patch applied)
#   /workspace/fixed  55864a0a2a8833b9b00b17236aa93c1b859051ef   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.13.13 (main, Jun  2 2026, 22:27:49) [Clang 22.1.3 ]
#   numpy    2.4.6
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
