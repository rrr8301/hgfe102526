# scipy-23019 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-23019:eradyn-agent-gpt validate
#
FROM python:3.12

# both revisions, as the reproduction left them
#   /workspace/buggy  01fd29031eff45a6527adce1782bc53e0df37a2c   (+ the developer test patch applied)
#   /workspace/fixed  9930630d65ad7167b02f10f750da91a5708ce844   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.15 (main, Oct 21 2026, 17:47:06) [Clang 23.1.3 ]
#   numpy    2.3.1
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
