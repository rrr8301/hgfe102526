# scipy-24462 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-24462:eradyn-agent validate
#
FROM python:3.14

# both revisions, as the reproduction left them
#   /workspace/buggy  f835efb16637ac667a43337720340d3f7c7716d0   (+ the developer test patch applied)
#   /workspace/fixed  e987feb77a4d2fc742097f4b9927a5249b6afc96   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.14.9 (main, Oct 21 2026, 17:48:02) [Clang 23.1.3 ]
#   numpy    2.4.1
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
