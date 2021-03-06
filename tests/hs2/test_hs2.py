#!/usr/bin/env python
# Copyright (c) 2012 Cloudera, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Client tests for Impala's HiveServer2 interface

import pytest
from tests.hs2.hs2_test_suite import HS2TestSuite, needs_session, operation_id_to_query_id
from TCLIService import TCLIService
from ImpalaService import ImpalaHiveServer2Service
from ExecStats.ttypes import TExecState

class TestHS2(HS2TestSuite):
  def test_open_session(self):
    """Check that a session can be opened"""
    open_session_req = TCLIService.TOpenSessionReq()
    TestHS2.check_response(self.hs2_client.OpenSession(open_session_req))

  def test_open_session_unsupported_protocol(self):
    """Test that we get the right protocol version back if we ask for one larger than the
    server supports. This test will fail as we support newer version of HS2, and should be
    updated."""
    open_session_req = TCLIService.TOpenSessionReq()
    open_session_req.protocol_version = \
        TCLIService.TProtocolVersion.HIVE_CLI_SERVICE_PROTOCOL_V7
    open_session_resp = self.hs2_client.OpenSession(open_session_req)
    TestHS2.check_response(open_session_resp)
    assert open_session_resp.serverProtocolVersion == \
        TCLIService.TProtocolVersion.HIVE_CLI_SERVICE_PROTOCOL_V6

  def test_close_session(self):
    """Test that an open session can be closed"""
    open_session_req = TCLIService.TOpenSessionReq()
    resp = self.hs2_client.OpenSession(open_session_req)
    TestHS2.check_response(resp)

    close_session_req = TCLIService.TCloseSessionReq()
    close_session_req.sessionHandle = resp.sessionHandle
    TestHS2.check_response(self.hs2_client.CloseSession(close_session_req))

  def test_double_close_session(self):
    """Test that an already closed session cannot be closed a second time"""
    open_session_req = TCLIService.TOpenSessionReq()
    resp = self.hs2_client.OpenSession(open_session_req)
    TestHS2.check_response(resp)

    close_session_req = TCLIService.TCloseSessionReq()
    close_session_req.sessionHandle = resp.sessionHandle
    TestHS2.check_response(self.hs2_client.CloseSession(close_session_req))

    # Double close should be an error
    TestHS2.check_response(self.hs2_client.CloseSession(close_session_req),
                           TCLIService.TStatusCode.ERROR_STATUS)

  @needs_session()
  def test_get_operation_status(self):
    """Tests that GetOperationStatus returns a valid result for a running query"""
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.statement = "SELECT COUNT(*) FROM functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    TestHS2.check_response(execute_statement_resp)

    get_operation_status_req = TCLIService.TGetOperationStatusReq()
    get_operation_status_req.operationHandle = execute_statement_resp.operationHandle

    get_operation_status_resp = \
        self.hs2_client.GetOperationStatus(get_operation_status_req)
    TestHS2.check_response(get_operation_status_resp)

    assert get_operation_status_resp.operationState in \
        [TCLIService.TOperationState.INITIALIZED_STATE,
         TCLIService.TOperationState.RUNNING_STATE,
         TCLIService.TOperationState.FINISHED_STATE]

  @needs_session()
  def test_malformed_get_operation_status(self):
    """Tests that a short guid / secret returns an error (regression would be to crash
    impalad)"""
    operation_handle = TCLIService.TOperationHandle()
    operation_handle.operationId = TCLIService.THandleIdentifier()
    operation_handle.operationId.guid = "short"
    operation_handle.operationId.secret = "short_secret"
    assert len(operation_handle.operationId.guid) != 16
    assert len(operation_handle.operationId.secret) != 16
    operation_handle.operationType = TCLIService.TOperationType.EXECUTE_STATEMENT
    operation_handle.hasResultSet = False

    get_operation_status_req = TCLIService.TGetOperationStatusReq()
    get_operation_status_req.operationHandle = operation_handle

    get_operation_status_resp = \
        self.hs2_client.GetOperationStatus(get_operation_status_req)
    TestHS2.check_response(get_operation_status_resp,
                           TCLIService.TStatusCode.ERROR_STATUS)
    err_msg = "(guid size: %d, expected 16, secret size: %d, expected 16)" \
        % (len(operation_handle.operationId.guid),
           len(operation_handle.operationId.secret))
    assert err_msg in get_operation_status_resp.status.errorMessage

  @pytest.mark.execute_serially
  def test_socket_close_forces_session_close(self):
    """Test that closing the underlying socket forces the associated session to close.
    See IMPALA-564"""
    open_session_req = TCLIService.TOpenSessionReq()
    resp = self.hs2_client.OpenSession(open_session_req)
    TestHS2.check_response(resp)
    num_sessions = self.impalad_test_service.get_metric_value(
      "impala-server.num-open-hiveserver2-sessions")

    assert num_sessions > 0

    self.socket.close()
    self.socket = None
    self.impalad_test_service.wait_for_metric_value(
      "impala-server.num-open-hiveserver2-sessions", num_sessions - 1)

  @pytest.mark.execute_serially
  def test_multiple_sessions(self):
    """Test that multiple sessions on the same socket connection are allowed"""
    num_sessions = self.impalad_test_service.get_metric_value(
      "impala-server.num-open-hiveserver2-sessions")
    session_ids = []
    for _ in xrange(5):
      open_session_req = TCLIService.TOpenSessionReq()
      resp = self.hs2_client.OpenSession(open_session_req)
      TestHS2.check_response(resp)
      # Check that all sessions get different IDs
      assert resp.sessionHandle not in session_ids
      session_ids.append(resp.sessionHandle)

    self.impalad_test_service.wait_for_metric_value(
      "impala-server.num-open-hiveserver2-sessions", num_sessions + 5)

    self.socket.close()
    self.socket = None
    self.impalad_test_service.wait_for_metric_value(
      "impala-server.num-open-hiveserver2-sessions", num_sessions)

  @needs_session()
  def test_get_schemas(self):
    get_schemas_req = TCLIService.TGetSchemasReq()
    get_schemas_req.sessionHandle = self.session_handle
    get_schemas_resp = self.hs2_client.GetSchemas(get_schemas_req)
    TestHS2.check_response(get_schemas_resp)
    fetch_results_req = TCLIService.TFetchResultsReq()
    fetch_results_req.operationHandle = get_schemas_resp.operationHandle
    fetch_results_req.maxRows = 100
    fetch_results_resp = self.hs2_client.FetchResults(fetch_results_req)
    TestHS2.check_response(fetch_results_resp)
    query_id = operation_id_to_query_id(get_schemas_resp.operationHandle.operationId)
    profile_page = self.impalad_test_service.read_query_profile_page(query_id)

    # Test fix for IMPALA-619
    assert "Sql Statement: GET_SCHEMAS" in profile_page
    assert "Query Type: DDL" in profile_page

  def get_log(self, query_stmt):
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.statement = query_stmt
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    TestHS2.check_response(execute_statement_resp)

    # Fetch results to make sure errors are generated
    fetch_results_req = TCLIService.TFetchResultsReq()
    fetch_results_req.operationHandle = execute_statement_resp.operationHandle
    fetch_results_req.maxRows = 100
    fetch_results_resp = self.hs2_client.FetchResults(fetch_results_req)
    TestHS2.check_response(fetch_results_resp)

    get_log_req = TCLIService.TGetLogReq()
    get_log_req.operationHandle = execute_statement_resp.operationHandle
    get_log_resp = self.hs2_client.GetLog(get_log_req)
    TestHS2.check_response(get_log_resp)
    return get_log_resp.log

  @needs_session()
  def test_get_log(self):
    # Test query that generates analysis warnings
    log = self.get_log("compute stats functional.decimal_tbl")
    assert "Decimal column stats not yet supported" in log

    # Test query that generates BE warnings
    log = self.get_log("select * from functional.alltypeserror")
    assert "Error converting column" in log

    # Test overflow warning
    log = self.get_log("select cast(1000 as decimal(2, 1))")
    assert "Expression overflowed, returning NULL" in log

    # Test CDH4 decimal warnings
    TEST_TABLE = "%s.get_log_test" % self.TEST_DB
    LOG_MSG = "DECIMAL columns are not supported by every component of CDH4"
    try:
      log = self.get_log("create table %s(a decimal)" % TEST_TABLE)
      assert LOG_MSG in log
      log = self.get_log("alter table %s add columns (b int, c decimal)" % TEST_TABLE)
      assert LOG_MSG in log
      log = self.get_log("alter table %s change b b decimal(2,1)" % TEST_TABLE)
    finally:
      self.client.execute("drop table if exists %s.get_log_test" % self.TEST_DB)

  @needs_session()
  def test_get_exec_summary(self):
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.statement = "SELECT COUNT(1) FROM functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    TestHS2.check_response(execute_statement_resp)

    exec_summary_req = ImpalaHiveServer2Service.TGetExecSummaryReq()
    exec_summary_req.operationHandle = execute_statement_resp.operationHandle
    exec_summary_req.sessionHandle = self.session_handle
    exec_summary_resp = self.hs2_client.GetExecSummary(exec_summary_req)

    # Test getting the summary while query is running. We can't verify anything
    # about the summary (depends how much progress query has made) but the call
    # should work.
    TestHS2.check_response(exec_summary_resp)

    close_operation_req = TCLIService.TCloseOperationReq()
    close_operation_req.operationHandle = execute_statement_resp.operationHandle
    TestHS2.check_response(self.hs2_client.CloseOperation(close_operation_req))

    exec_summary_resp = self.hs2_client.GetExecSummary(exec_summary_req)
    TestHS2.check_response(exec_summary_resp)
    assert len(exec_summary_resp.summary.nodes) > 0

  @needs_session()
  def test_get_profile(self):
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.statement = "SELECT COUNT(2) FROM functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    TestHS2.check_response(execute_statement_resp)

    get_profile_req = ImpalaHiveServer2Service.TGetRuntimeProfileReq()
    get_profile_req.operationHandle = execute_statement_resp.operationHandle
    get_profile_req.sessionHandle = self.session_handle
    get_profile_resp = self.hs2_client.GetRuntimeProfile(get_profile_req)
    TestHS2.check_response(get_profile_resp)
    assert execute_statement_req.statement in get_profile_resp.profile

    close_operation_req = TCLIService.TCloseOperationReq()
    close_operation_req.operationHandle = execute_statement_resp.operationHandle
    TestHS2.check_response(self.hs2_client.CloseOperation(close_operation_req))

    get_profile_resp = self.hs2_client.GetRuntimeProfile(get_profile_req)
    TestHS2.check_response(get_profile_resp)

    assert execute_statement_req.statement in get_profile_resp.profile
