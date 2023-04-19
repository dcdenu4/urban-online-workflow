import React, { useState } from 'react';

import {
  Button,
  ProgressBar,
} from '@blueprintjs/core';

import useInterval from '../hooks/useInterval';
import {
  getJobStatus,
  runInvest,
} from '../requests';

export default function InvestRunner(props) {
  const {
    scenarios,
    refreshScenarios,
  } = props;
  const [jobIDs, setJobIDs] = useState([]);
  const [progress, setProgress] = useState(0);
  const [nJobs, setNJobs] = useState(null);
  const [progressState, setProgressState] = useState('success');

  useInterval(async () => {
    const statuses = await Promise.all(jobIDs.map((id) => getJobStatus(id)));
    const pendingJobs = [...jobIDs];
    statuses.forEach((status, idx) => {
      if (['success', 'failed'].includes(status)) {
        pendingJobs.splice(idx, 1);
        setProgress((nJobs - pendingJobs.length) / nJobs);
        if (status === 'failed') { setProgressState('warning'); }
      }
    });
    setJobIDs(pendingJobs);
    if (!pendingJobs.length) {
      refreshScenarios(); // assuming results are in scenario db
    }
  }, (jobIDs.length) ? 2000 : null);

  const handleClick = async () => {
    setProgress(0);
    const jobs = await Promise.all(
      scenarios.map((scenario) => runInvest(scenario.scenario_id))
    );
    const jids = jobs.map((j) => Object.values(j)).flat();
    setJobIDs(jids);
    setNJobs(jids.length);
  };

  return (
    <div id="invest-runner">
      <Button
        onClick={handleClick}
      >
        Run InVEST Models
      </Button>
      {
        (jobIDs.length || progress === 1)
          ? (
            <ProgressBar
              value={progress}
              intent={progressState}
              animate={false}
            />
          )
          : <div />
      }
    </div>
  );
}
