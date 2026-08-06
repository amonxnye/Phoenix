# An explicit runtime, because the Builder's core dependency is a binary.
#
# Two build-detection attempts guessed wrong about whether git survives into the
# runtime image, and the failure mode is bad: the page serves perfectly and the
# agents cannot work, because every attempt runs in a `git worktree` and the branch
# is the only undo. So the environment is stated here rather than inferred.

FROM python:3.11-slim

# git is runtime, not build-time. ca-certificates so the model seam can reach an API.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The agents commit inside their worktrees, and a commit with no identity fails.
# starter.py sets these per repository too; this is the backstop.
RUN git config --system user.email "agent@phoenix.local" \
 && git config --system user.name "Phoenix Agent" \
 && git config --system init.defaultBranch main \
 && git config --system --add safe.directory '*'

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Unbuffered, so a platform's log tail shows the run log as it happens rather than
# whenever a buffer happens to flush.
ENV PYTHONUNBUFFERED=1 \
    GATE_TRUST_PROXY=1

EXPOSE 8788

# gate.py reads $PORT and falls back to 8788. Guests get a workspace each; see
# SERVICE.md for what bounds them and DEPLOY.md for what to set.
CMD ["python", "gov/gate.py", "--serve", "--host", "0.0.0.0"]
