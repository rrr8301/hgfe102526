# scikit-learn-33020 — NumBugBench instance, REBUILD FROM SOURCE
#
# Unlike the Dockerfile inside the published image, this one reconstructs the
# instance: it clones both revisions, applies the developer test, installs each
# revision's frozen dependencies and builds the subject from its checkout.
#
# The final build step runs `validate.sh`, which exits non-zero unless
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS
# so THIS IMAGE CANNOT BE BUILT UNLESS THE F2P REPRODUCES. A dependency that
# drifts into an environment where the bug does not manifest breaks the build
# rather than yielding a silently wrong instance.
#
#   docker build -t scikit-learn-33020:rebuilt .
#   docker run --rm scikit-learn-33020:rebuilt validate
#
# PR: https://github.com/scikit-learn/scikit-learn/pull/33020
FROM python:3.11

ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gfortran git pkg-config cmake ninja-build libopenblas-dev liblapack-dev patch \
 && rm -rf /var/lib/apt/lists/*

# ---- the two revisions, at the commits the reproduction validated ----------
ARG REPO=https://github.com/scikit-learn/scikit-learn.git
ARG BUGGY=c83733128e2ca835674442927dfbbe2c3da93db0
ARG FIXED=ce4f2340fecb280fc33163a1437042e1d98f2356
RUN git clone --no-checkout --filter=blob:none "$REPO" /workspace/buggy \
 && git -C /workspace/buggy checkout --detach "$BUGGY" \
 && git -C /workspace/buggy submodule update --init --recursive --depth 1 || true
RUN git clone --no-checkout --filter=blob:none "$REPO" /workspace/fixed \
 && git -C /workspace/fixed checkout --detach "$FIXED" \
 && git -C /workspace/fixed submodule update --init --recursive --depth 1 || true

# ---- the developer test, on BOTH revisions ---------------------------------
# The test is the oracle, so it must be identical on the two sides; only the
# source under it differs. `git apply` is tried first, then `patch`, because a
# test file that does not yet exist on the buggy side is an add, not a change.
COPY developer_test.patch /tmp/developer_test.patch
RUN for d in /workspace/buggy /workspace/fixed; do \
      cd "$d" && \
      ( git apply --3way /tmp/developer_test.patch \
        || git apply /tmp/developer_test.patch \
        || patch -p1 --forward < /tmp/developer_test.patch \
        || true ); \
    done

# ---- one frozen environment per revision -----------------------------------
# The subject itself is stripped from the lockfile: a freeze records it as a
# local path (`scikit\-learn @ file:///workspace/...`), which pip cannot resolve,
# and it must come from the checkout so that each side builds ITS OWN source.
#
# Build ISOLATION stays on for the first two attempts. Turning it off — which
# a compiled project sometimes needs, to build against the pinned numpy rather
# than a fresh one — starves a project of its build-time requirements:
# mpmath#1107 needs setuptools_scm to write `mpmath/_version.py`, which is
# generated rather than committed, and without it `import mpmath` fails and the
# build correctly refused to finish. So no-isolation is the LAST resort.
COPY env/ /benchmark/env/
RUN set -eux; for rev in buggy fixed; do \
      d=/workspace/$rev; \
      python -m venv "$d/.venv"; \
      grep -vEi '^(scikit\-learn[[:space:]]*(==|@|$)|numagent[_-]|-e |#|$)' /benchmark/env/$rev/requirements.lock \
        | grep -vE '@ file://' > /tmp/$rev.lock; \
      "$d/.venv/bin/pip" install --no-input -q -r /tmp/$rev.lock; \
      "$d/.venv/bin/pip" install --no-input -q -e "$d" --no-deps \
        || "$d/.venv/bin/pip" install --no-input -q "$d" --no-deps \
        || "$d/.venv/bin/pip" install --no-input -q -e "$d" --no-deps \
             --no-build-isolation; \
    done

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

# ---- the guarantee ---------------------------------------------------------
RUN /benchmark/scripts/validate.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
